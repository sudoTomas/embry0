"""Leads API (spec 2026-10-25): the list, a pass by hand, leads in the proposals feed, and each kind's yes."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from embry0.api.v1 import leads as leads_api
from embry0.api.v1 import proposals as proposals_api
from embry0.execution.pull_requests import PullRequest
from embry0.services.comment_dispatch import CommentDispatchService
from embry0.services.proposals import Decision
from tests.fixtures.leads import MemorySettings

T0 = datetime(2026, 10, 25, 12, tzinfo=UTC)
HEADERS = {"X-Requested-With": "XMLHttpRequest"}


def _pull_request(head_ref: str = "feat/export", *, from_fork: bool = False) -> PullRequest:
    """The review lead's pull request as the forge reads it now: open, on ``head_ref``, the default branch main."""
    return PullRequest(42, "open", head_ref, "https://github.com/acme/web/pull/42", from_fork, default_branch="main")


def _row(kind: str, lead_id: str, **over: Any) -> dict[str, Any]:
    data = {
        "ci": {"check": "build", "sha": "a" * 40, "branch": "main", "source": "check-run", "paths": [], "labels": []},
        "review": {"number": 42, "head_ref": "feat/export", "threads": [], "paths": ["src/app.py"], "labels": []},
        "ticket": {"tracker": "github", "key": "9", "readiness": 70, "paths": [], "labels": ["bug"]},
    }[kind]
    return {
        "id": lead_id,
        "kind": kind,
        "repo": "acme/web",
        "source_key": "k",
        "title": f"A {kind} lead",
        "summary": f"The {kind} summary",
        "details": "What was seen",
        "url": "https://forge/x",
        "severity": "high",
        "effort": "medium",
        "action": "Start a job",
        "area": "Billing" if kind == "ci" else None,
        "data": dict(data),
        "state": "open",
        "first_seen": T0,
        "last_seen": T0,
        "gone_at": None,
        **over,
    }


class FakeLeads:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = {r["id"]: r for r in rows}

    async def open_leads(self) -> list[dict[str, Any]]:
        return [r for r in self.rows.values() if r["state"] == "open"]

    async def get(self, lead_id: str) -> dict[str, Any] | None:
        return self.rows.get(lead_id)

    async def list(self, **kw: Any) -> list[dict[str, Any]]:
        return [r for r in self.rows.values() if kw.get("kind") in (None, r["kind"])]


class FakeProposals:
    def __init__(self) -> None:
        self.recorded: list[dict[str, Any]] = []
        self.answers: dict[str, Decision] = {}

    async def cases(self) -> list[Any]:
        return []

    async def issues(self) -> list[Any]:
        return []

    async def audits(self) -> list[Any]:
        return []

    async def owners(self) -> dict[str, list[str]]:
        return {"acme/web": ["octo"]}

    async def decisions(self) -> dict[str, Decision]:
        return self.answers

    async def record(self, **row: Any) -> None:
        self.recorded.append(row)
        self.answers[row["proposal_id"]] = Decision(row["proposal_id"], row["decision"], row["decided_by"], T0)


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    app = FastAPI()
    app.include_router(proposals_api.router, prefix="/api/v1")
    app.include_router(leads_api.router, prefix="/api/v1")
    st = app.state
    st.leads_repo = FakeLeads(
        [_row("ci", "ci:1"), _row("review", "review:1"), _row("ticket", "ticket:1"), _row("ci", "ci:2", state="gone")]
    )
    st.proposals_repo = FakeProposals()
    st.config = SimpleNamespace(audit_log_path=None)
    st.db = None
    st.settings = MemorySettings()
    st.issues_repo = MagicMock()
    st.issues_repo.create = AsyncMock(return_value="iss-new")
    st.issues_repo.update = AsyncMock()
    st.issues_repo.get_by_tracker_ref = AsyncMock(return_value=None)
    st.issue_executor = MagicMock()
    st.issue_executor.execute = AsyncMock(return_value="job-issue")
    st.issue_executor.dispatch_refusal = AsyncMock(return_value=None)
    st.jobs_repo = MagicMock()
    st.jobs_repo.create = AsyncMock(return_value="job-review")
    st.jobs_repo.update = AsyncMock()
    st.jobs_repo.find_by_pr_url = AsyncMock(return_value=[])
    # A review lead starts as a tagged comment does: the pull request is read again (here, as the forge answers).
    st.comment_dispatch = CommentDispatchService(
        config=SimpleNamespace(github_token=""),
        jobs_repo=st.jobs_repo,
        agent_sessions_repo=MagicMock(get=AsyncMock(return_value=None)),
        executor=st.issue_executor,
    )
    st.comment_dispatch._fetch_pr = AsyncMock(return_value=_pull_request())
    st.github_issues_client = SimpleNamespace(
        get_issue=AsyncMock(
            return_value={"state": "open", "title": "Export fails", "body": "b", "html_url": "u", "labels": []}
        )
    )

    async def no_audit(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(proposals_api, "emit_audit", no_audit)
    return app


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_the_list_shows_what_a_view_needs(app: FastAPI) -> None:
    async with await _client(app) as client:
        body = (await client.get("/api/v1/leads", params={"kind": "ci"})).json()
        bad = await client.get("/api/v1/leads", params={"kind": "rumor"})
    first = body["leads"][0]
    assert first["severity"] == "high" and first["effort"] == "medium" and first["area"] == "Billing"
    assert first["first_seen"] == T0.isoformat() and first["source"]["check"] == "build"
    assert bad.status_code == 422


async def test_open_leads_are_asked_like_every_proposal_with_area_and_owners(app: FastAPI) -> None:
    async with await _client(app) as client:
        body = (await client.get("/api/v1/proposals")).json()
        only_ci = (await client.get("/api/v1/proposals", params={"kind": "ci"})).json()
    kinds = sorted(p["kind"] for p in body["proposals"])
    assert kinds == ["ci", "review", "ticket"], "a gone lead is not asked"
    ci = next(p for p in body["proposals"] if p["kind"] == "ci")
    assert ci["area"] == "Billing" and ci["owners"] == ["octo"] and ci["severity"] == "high"
    assert ci["summary"] == "The ci summary" and ci["action"] == "Start a job"
    assert [p["id"] for p in only_ci["proposals"]] == ["ci:1"]


async def test_a_yes_to_a_ci_lead_files_its_task_as_an_issue_and_runs_it(app: FastAPI) -> None:
    async with await _client(app) as client:
        response = await client.post("/api/v1/proposals/ci:1/accept", json={"by": "octo"}, headers=HEADERS)
    assert response.status_code == 202, response.text
    assert response.json()["job_id"] == "job-issue"
    created = app.state.issues_repo.create.await_args.kwargs
    assert created["labels"] == ["embry0-lead", "lead:ci", "area:billing"] and created["repo"] == "acme/web"
    assert created["body"].startswith("Fix the check build failing on main of acme/web.")
    assert "Do not disable, skip or loosen the check." in created["body"]
    app.state.issue_executor.execute.assert_awaited_once()
    assert app.state.proposals_repo.recorded[0]["kind"] == "ci"


async def test_a_yes_to_a_review_lead_follows_up_on_the_pull_request_branch(app: FastAPI) -> None:
    async with await _client(app) as client:
        response = await client.post("/api/v1/proposals/review:1/accept", json={"by": "octo"}, headers=HEADERS)
    assert response.status_code == 202, response.text
    created = app.state.jobs_repo.create.await_args.kwargs
    assert created["pipeline_template"] == "pr-followup" and created["dispatched_by"] == "octo"
    assert created["context"] == {"type": "git", "repo": "acme/web", "ref": "feat/export"}
    app.state.jobs_repo.update.assert_awaited_once_with("job-review", pr_url="https://github.com/acme/web/pull/42")
    assert app.state.issue_executor.submit_job.call_args.kwargs["kind"] == "lead"
    assert app.state.comment_dispatch._fetch_pr.await_args.args == ("acme/web", 42, "github")


@pytest.mark.parametrize(
    ("read", "reason"),
    [
        (_pull_request("main"), "pr head is default branch"),
        (_pull_request(from_fork=True), "pr from fork"),
        (None, "pr fetch failed"),
    ],
    ids=["head-is-default-branch", "fork", "unread"],
)
async def test_a_review_lead_follows_up_only_where_a_tagged_comment_would(
    app: FastAPI, read: PullRequest | None, reason: str
) -> None:
    """The branch the lead stored is not trusted: the pull request is read again, and one whose head is the default
    branch (a release pull request from main, say), a fork's, or one that cannot be read gets no job."""
    app.state.comment_dispatch._fetch_pr = AsyncMock(return_value=read)
    async with await _client(app) as client:
        response = await client.post("/api/v1/proposals/review:1/accept", json={"by": "octo"}, headers=HEADERS)
    assert (response.status_code, response.json()["detail"]) == (409, f"not followed up: {reason}"), response.text
    app.state.jobs_repo.create.assert_not_awaited()


@pytest.mark.parametrize("name", ["embry0:scout", " Embry0:watcher"])
async def test_an_answer_may_not_name_an_embry0_actor(app: FastAPI, name: str) -> None:
    """``by`` is the job's dispatcher; an embry0 actor's name would be counted as that actor (and skip its limits)."""
    async with await _client(app) as client:
        accept = await client.post("/api/v1/proposals/review:1/accept", json={"by": name}, headers=HEADERS)
        decline = await client.post("/api/v1/proposals/ci:1/decline", json={"by": name}, headers=HEADERS)
    assert (accept.status_code, decline.status_code) == (422, 422), (
        f"an answer naming an embry0 actor is refused: {accept.status_code} {decline.status_code}"
    )
    app.state.jobs_repo.create.assert_not_awaited()
    app.state.issue_executor.dispatch_refusal.assert_not_awaited()
    assert app.state.proposals_repo.recorded == [], "nothing is recorded under an embry0 actor's name"


@pytest.mark.parametrize("proposal", ["ci:1", "ticket:1", "review:1"])
async def test_a_start_the_gate_refuses_creates_nothing_and_records_no_answer(app: FastAPI, proposal: str) -> None:
    app.state.issue_executor.dispatch_refusal = AsyncMock(
        return_value=SimpleNamespace(public_message="dispatch refused: dispatch rate limit reached")
    )
    async with await _client(app) as client:
        response = await client.post(f"/api/v1/proposals/{proposal}/accept", json={"by": "octo"}, headers=HEADERS)
    assert response.status_code == 429 and "rate limit" in response.text
    app.state.issue_executor.dispatch_refusal.assert_awaited_once_with(actor="octo")
    app.state.issues_repo.create.assert_not_awaited()
    app.state.jobs_repo.create.assert_not_awaited()
    assert app.state.proposals_repo.recorded == [], "the lead stays open to answer again"


async def test_a_yes_to_a_github_ticket_brings_it_in_and_runs_it(app: FastAPI) -> None:
    async with await _client(app) as client:
        response = await client.post("/api/v1/proposals/ticket:1/accept", json={"by": "octo"}, headers=HEADERS)
    assert response.status_code == 202, response.text
    created = app.state.issues_repo.create.await_args.kwargs
    assert created["tracker"] == "github" and created["tracker_key"] == "9" and created["created_by"] == "lead-accept"


async def test_a_yes_to_a_linear_ticket_goes_through_its_label_path(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.state.leads_repo.rows["ticket:1"]["data"] = {"tracker": "linear", "key": "ENG-7", "paths": [], "labels": []}
    linear = SimpleNamespace(label_and_dispatch=AsyncMock(return_value={"action": "dispatched", "job_id": "job-lin"}))

    async def current(request: Any) -> Any:
        return linear

    async def context(request: Any, *, actor: str) -> Any:
        return SimpleNamespace(actor=actor, run_with=None)

    monkeypatch.setattr("embry0.api.trigger_context.current_linear_sync", current)
    monkeypatch.setattr("embry0.api.trigger_context.linear_trigger_context", context)
    async with await _client(app) as client:
        response = await client.post("/api/v1/proposals/ticket:1/accept", json={"by": "octo"}, headers=HEADERS)
    assert response.status_code == 202 and response.json()["job_id"] == "job-lin"
    args = linear.label_and_dispatch.await_args
    assert args.args[0] == "ENG-7" and args.kwargs == {"created_by": "lead-accept", "expected_repo": "acme/web"}


async def test_a_pass_by_hand_runs_the_loop_for_one_repository(app: FastAPI) -> None:
    app.state.leads_loop = SimpleNamespace(tick=AsyncMock(return_value={"acme/web": {"ci": {"found": 1}}}))
    async with await _client(app) as client:
        response = await client.post("/api/v1/leads/run", json={"repo": "acme/web", "backlog": True}, headers=HEADERS)
    assert response.json() == {"repos": {"acme/web": {"ci": {"found": 1}}}}
    app.state.leads_loop.tick.assert_awaited_once_with(["acme/web"], backlog=True)


async def test_a_leads_issue_is_labelled_as_its_organization_says_and_never_with_a_trigger_label(
    app: FastAPI,
) -> None:
    """#180: leads.labels as the lead's organization reads it; a label that became a trigger label after it was
    written, and a keyed one that is one, are left off."""
    app.state.settings = MemorySettings(organizations={"acme": {"labels": ["team-lead", "later"]}})
    app.state.integration_repo = SimpleNamespace(get_trigger_labels=AsyncMock(return_value=["LATER", "area:billing"]))
    async with await _client(app) as client:
        response = await client.post("/api/v1/proposals/ci:1/accept", json={"by": "octo"}, headers=HEADERS)
    assert response.status_code == 202, response.text
    labels = app.state.issues_repo.create.await_args.kwargs["labels"]
    assert labels == ["team-lead", "lead:ci"], f"the organization's labels, without a trigger label: {labels}"


def _gitlab_ticket(app: FastAPI, issue: dict[str, Any] | None, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """``ticket:1`` is a GitLab issue, 5 of ``acme/web``, of the active GitLab organization ``acme``: read with the
    organization's own client over a faked GitLab. The deployment's GitLab tracker would answer it open, so a read
    that used it would not be told apart. Returns the organizations whose clients were built."""
    from embry0.api.v1 import organizations as organizations_api
    from embry0.services.gitlab_sync import GitLabSyncService
    from tests.unit.services.leads.fakes import fake_gitlab

    app.state.leads_repo.rows["ticket:1"]["data"] = {"tracker": "gitlab", "key": "5", "paths": [], "labels": []}
    answers = {} if issue is None else {"/projects/acme%2Fweb/issues/5": issue}
    deployment = {"/projects/acme%2Fweb/issues/5": {"iid": 5, "state": "opened", "title": "the deployment's"}}
    app.state.gitlab_sync = GitLabSyncService(fake_gitlab(deployment))
    app.state.organizations_repo = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(owner="acme", forge="gitlab", status="active", credential_id=None))
    )
    built: list[Any] = []

    async def token_for(request: Any, owner: str, forge: str, credential_id: Any, **kwargs: Any) -> tuple[str, str]:
        return "token-of-acme", "env:TEST"

    def client(request: Any, org: Any, token: str) -> Any:
        built.append((org.owner, token))
        return fake_gitlab(answers)

    monkeypatch.setattr(organizations_api, "_token_for", token_for)
    monkeypatch.setattr(organizations_api, "make_pull_request_client", client)
    return built


async def test_a_yes_to_a_gitlab_ticket_brings_it_in_and_runs_it(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    built = _gitlab_ticket(
        app,
        {
            "iid": 5,
            "state": "opened",
            "title": "Export fails",
            "description": "b",
            "labels": ["bug"],
            "web_url": "https://gitlab.example.test/acme/web/-/issues/5",
        },
        monkeypatch,
    )
    async with await _client(app) as client:
        response = await client.post("/api/v1/proposals/ticket:1/accept", json={"by": "octo"}, headers=HEADERS)
    assert response.status_code == 202, response.text
    created = app.state.issues_repo.create.await_args.kwargs
    assert (created["tracker"], created["tracker_key"], created["repo"]) == ("gitlab", "5", "acme/web")
    assert created["tracker_url"] == "https://gitlab.example.test/acme/web/-/issues/5"
    assert created["title"] == "Export fails", "read with the organization's client, not the deployment's"
    assert built == [("acme", "token-of-acme")]
    app.state.issue_executor.execute.assert_awaited_once()


@pytest.mark.parametrize(
    ("issue", "status", "detail"),
    [
        ({"iid": 5, "state": "closed", "title": "t"}, 409, "acme/web#5 is closed on GitLab"),
        (None, 502, "GitLab could not serve acme/web#5"),
    ],
    ids=["closed", "unread"],
)
async def test_a_gitlab_ticket_closed_or_unread_now_is_not_started(
    app: FastAPI, issue: dict[str, Any] | None, status: int, detail: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gitlab_ticket(app, issue, monkeypatch)
    async with await _client(app) as client:
        response = await client.post("/api/v1/proposals/ticket:1/accept", json={"by": "octo"}, headers=HEADERS)
    assert (response.status_code, response.json()["detail"]) == (status, detail)
    app.state.issues_repo.create.assert_not_awaited()


async def test_the_status_says_why_a_finder_was_skipped_on_the_repositories_the_key_covers(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    skipped = {"skipped": "review reads github repositories; this one is on gitlab"}
    status = {
        "last_pass_at": "2026-10-25T12:00:00+00:00",
        "repos": {
            "acme/web": {"forge": "gitlab", "at": "2026-10-25T12:00:00+00:00", "kinds": {"review": skipped}},
            "other/api": {"forge": "github", "at": "2026-10-25T12:00:00+00:00", "kinds": {}},
        },
    }
    app.state.leads_loop = SimpleNamespace(status=lambda: status)
    monkeypatch.setattr(leads_api, "key_covers", lambda request, repo: repo == "acme/web")
    async with await _client(app) as client:
        response = await client.get("/api/v1/leads/status", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "last_pass_at": status["last_pass_at"],
        "repos": {"acme/web": status["repos"]["acme/web"]},
    }


@pytest.mark.parametrize(
    "org",
    [
        None,
        SimpleNamespace(owner="acme", forge="gitlab", status="inactive", credential_id=None),
        SimpleNamespace(owner="acme", forge="github", status="active", credential_id=None),
    ],
    ids=["missing", "inactive", "on-github"],
)
async def test_a_gitlab_ticket_of_no_active_gitlab_organization_is_not_read(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, org: Any
) -> None:
    built = _gitlab_ticket(app, {"iid": 5, "state": "opened", "title": "t"}, monkeypatch)
    app.state.organizations_repo.get = AsyncMock(return_value=org)
    async with await _client(app) as client:
        response = await client.post("/api/v1/proposals/ticket:1/accept", json={"by": "octo"}, headers=HEADERS)
    assert (response.status_code, response.json()["detail"]) == (
        409,
        "acme/web has no active GitLab organization to read it with",
    )
    assert built == [] and not app.state.issues_repo.create.await_count
