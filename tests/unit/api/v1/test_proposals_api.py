"""Owner proposals API (spec 2026-10-24): the feed with its owners, and each answer through its kind's own path."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs

from embry0.api.v1 import proposals as proposals_api
from embry0.services.context_audit import FIX_ACTOR
from embry0.services.proposals import Decision
from tests.fixtures.budget_settings import BudgetStore
from tests.fixtures.leads import MemorySettings

T0 = datetime(2026, 10, 24, 12, tzinfo=UTC)
REPORT = "## Findings\n\n| Severity | Location | Claim | Evidence | Fix |\n|---|---|---|---|---|\n| misleads | AGENTS.md:3 | Run make check | no target | say make test |\n"


class FakeRepo:
    def __init__(self) -> None:
        self.recorded: list[dict[str, Any]] = []
        self.answers: dict[str, Decision] = {}

    async def cases(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "hc-1",
                "repo": "acme/api",
                "title": "Timeouts",
                "severity": "high",
                "occurrence_count": 3,
                "created_at": T0,
                "tracked_at": T0,
            }
        ]

    async def issues(self) -> list[dict[str, Any]]:
        return [{"id": "iss-1", "repo": "acme/web", "title": "Fix login", "created_at": T0 + timedelta(hours=1)}]

    async def audits(self) -> list[dict[str, Any]]:
        return [
            {"repo": "acme/docs", "job_id": "job-a", "finished_at": T0 - timedelta(hours=1), "result_summary": REPORT}
        ]

    async def owners(self) -> dict[str, list[str]]:
        return {"acme/api": ["octo"], "acme/web": ["octo", "pat"]}

    async def decisions(self) -> dict[str, Decision]:
        return self.answers

    async def record(self, **row: Any) -> None:
        self.recorded.append(row)
        self.answers[row["proposal_id"]] = Decision(
            row["proposal_id"], row["decision"], row["decided_by"], datetime.now(UTC), row.get("until")
        )


class FakeForge:
    async def get(self, path: str, **_: Any) -> Any:
        if path == "/repos/acme/docs/contents/.github/CODEOWNERS":
            body = {"content": base64.b64encode(b"* @docs-owner @acme/team\n").decode()}
            return SimpleNamespace(status_code=200, json=lambda: body)
        return SimpleNamespace(status_code=404, json=lambda: {})


class FakeOrgs:
    """acme, registered on ``forge``, with ``status``."""

    def __init__(self, forge: str = "github", status: str = "active") -> None:
        self.forge = forge
        self.status = status

    async def get(self, owner: str) -> Any:
        return SimpleNamespace(owner=owner, forge=self.forge, status=self.status) if owner == "acme" else None

    async def list_all(self) -> list[Any]:
        return [SimpleNamespace(owner="acme", forge=self.forge, status=self.status)]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    app = FastAPI()
    app.include_router(proposals_api.router, prefix="/api/v1")
    app.state.proposals_repo = FakeRepo()
    app.state.organizations_repo = FakeOrgs()
    app.state.config = SimpleNamespace(audit_log_path=None)
    app.state.db = None
    app.state.settings = MemorySettings()

    async def forge_client(request: Any, org: Any) -> FakeForge:
        return FakeForge()

    # CODEOWNERS is read with the organization's own forge's client; a finding's fix reads GitHub's.
    monkeypatch.setattr("embry0.api.v1.organizations.org_own_forge_client", forge_client)
    monkeypatch.setattr("embry0.api.v1.organizations.org_forge_client", forge_client)

    async def no_audit(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(proposals_api, "emit_audit", no_audit)
    return app


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_the_feed_lists_every_kind_newest_first_with_its_owners(app: FastAPI) -> None:
    async with await _client(app) as client:
        body = (await client.get("/api/v1/proposals")).json()
    assert [p["kind"] for p in body["proposals"]] == ["issue", "case", "finding"]
    owners = {p["repo"]: p["owners"] for p in body["proposals"]}
    assert owners == {"acme/web": ["octo", "pat"], "acme/api": ["octo"], "acme/docs": ["docs-owner"]}, (
        "preferences first, else CODEOWNERS (teams skipped), each as written"
    )
    finding = body["proposals"][2]
    assert finding["id"].startswith("finding:") and finding["path"] == "AGENTS.md" and finding["line"] == 3


class _Registry:
    """The repository registry: every repository of acme registered on ``forge``, or none registered (``registered``
    false) and ``forge`` the configured default."""

    def __init__(self, forge: str, *, registered: bool = True) -> None:
        self.forge = forge
        self.registered = registered

    async def registered_as(self, repo: str) -> str | None:
        return repo if self.registered else None

    async def forge_or_none(self, repo: str) -> str:
        return self.forge


GITLAB_CODEOWNERS = """\
# The default section
* @root-owner
docs/ @x

[Docs] @dora
*
^[Ops][2] @ops-default
* @@maintainer @acme/platform/sre @erin_e someone@example.test
[docs]
/** @dan.dev
"""


async def test_a_gitlab_repository_s_owners_come_from_its_preference_and_its_codeowners_on_gitlab(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#181: an organization on GitLab: its CODEOWNERS is read through the GitLab client with its credential, in
    GitLab's order of places and with its sections; groups, roles and emails are left out, each with why. Owners
    are as written: a bare one is the organization's forge's, read so where it is resolved (owner_people)."""
    from tests.unit.services.leads.fakes import fake_gitlab

    asked: list[str] = []
    gitlab = fake_gitlab(
        {
            "/projects/acme%2Fdocs": {"default_branch": "trunk"},
            "/projects/acme%2Fdocs/repository/files/docs%2FCODEOWNERS/raw": GITLAB_CODEOWNERS.encode(),
        },
        asked,
    )
    made: list[str] = []

    async def forge_client(request: Any, org: Any) -> Any:
        made.append(f"{org.owner} on {org.forge}")
        return gitlab

    monkeypatch.setattr("embry0.api.v1.organizations.org_own_forge_client", forge_client)
    app.state.organizations_repo = FakeOrgs(forge="gitlab")
    app.state.repos_repo = _Registry("gitlab")
    async with await _client(app) as client:
        resp = await client.get("/api/v1/proposals")
    assert resp.status_code == 200, resp.text
    owners = {p["repo"]: p["owners"] for p in resp.json()["proposals"]}
    assert owners == {
        "acme/web": ["octo", "pat"],
        "acme/api": ["octo"],
        "acme/docs": ["root-owner", "gitlab:dan.dev", "gitlab:erin_e"],
    }, "CODEOWNERS read as GitLab reads it: bare where a bare owner may be the login, else gitlab:<username>"
    assert made == ["acme on gitlab"]
    files = [path.split("/repository/files/")[1] for path in asked if "/repository/files/" in path]
    assert files == ["CODEOWNERS/raw", "docs%2FCODEOWNERS/raw"], "GitLab's order: the root, then docs/, then .gitlab/"


async def test_a_repository_on_another_forge_than_its_organization_s_reads_no_codeowners(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    class Recording(FakeForge):
        """Answers every read GitHub's way and GitLab's, and keeps each one asked."""

        async def get(self, path: str, **kwargs: Any) -> Any:
            asked.append(path)
            return await super().get(path, **kwargs)

        async def get_project(self, repo: str) -> dict[str, Any]:
            asked.append(f"project {repo}")
            return {"default_branch": "main"}

        def project_path(self, repo: str, suffix: str = "") -> str:
            return f"/projects/{repo}{suffix}"

    async def forge_client(request: Any, org: Any) -> FakeForge:
        return Recording()

    monkeypatch.setattr("embry0.api.v1.organizations.org_own_forge_client", forge_client)
    app.state.repos_repo = _Registry("gitlab")  # acme is on GitHub
    with capture_logs() as logs:
        async with await _client(app) as client:
            resp = await client.get("/api/v1/proposals")
    assert resp.status_code == 200, resp.text
    docs = next(p for p in resp.json()["proposals"] if p["repo"] == "acme/docs")
    assert (docs["owners"], docs.get("owners_unread")) == ([], True), (
        "a GitLab repository is not read with its GitHub organization's client, and its owners are not known"
    )
    assert asked == [], f"nothing is asked of the organization's forge for it: {asked}"
    said = [e for e in logs if e["event"] == "codeowners_not_read_other_forge"]
    assert [(e["repo"], e["repo_forge"], e["organization_forge"]) for e in said] == [("acme/docs", "gitlab", "github")]


async def test_an_unregistered_repository_of_a_gitlab_organization_is_read_on_gitlab(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repository nobody registered is its organization's forge's, not the configured default's (GitHub)."""
    from tests.unit.services.leads.fakes import fake_gitlab

    gitlab = fake_gitlab(
        {
            "/projects/acme%2Fdocs": {"default_branch": "main"},
            "/projects/acme%2Fdocs/repository/files/CODEOWNERS/raw": b"* @root-owner\n",
        }
    )

    async def forge_client(request: Any, org: Any) -> Any:
        return gitlab

    monkeypatch.setattr("embry0.api.v1.organizations.org_own_forge_client", forge_client)
    app.state.organizations_repo = FakeOrgs(forge="gitlab")
    app.state.repos_repo = _Registry("github", registered=False)
    async with await _client(app) as client:
        resp = await client.get("/api/v1/proposals")
    owners = {p["repo"]: p["owners"] for p in resp.json()["proposals"]}
    assert owners["acme/docs"] == ["root-owner"]


async def test_a_gitlab_organization_s_finding_is_not_fixed_with_the_github_reader(app: FastAPI) -> None:
    async with await _client(app) as client:
        finding_id = next(
            p["id"] for p in (await client.get("/api/v1/proposals")).json()["proposals"] if p["kind"] == "finding"
        )
        app.state.organizations_repo = FakeOrgs(forge="gitlab")
        refused = await client.post(f"/api/v1/proposals/{finding_id}/accept", json={"by": "docs-owner"})
    assert (refused.status_code, refused.json()["detail"]) == (
        422,
        "fixing a finding is not supported for this forge (gitlab)",
    )


async def test_the_feed_filters_by_an_owner_on_its_forge(app: FastAPI) -> None:
    """#181: a bare owner of a GitLab organization's repository is a GitLab login; a bare filter is a GitHub one."""
    app.state.organizations_repo = FakeOrgs(forge="gitlab")
    async with await _client(app) as client:
        on_gitlab = (await client.get("/api/v1/proposals", params={"owner": "gitlab:@Octo"})).json()
        on_github = (await client.get("/api/v1/proposals", params={"owner": "octo"})).json()
    assert sorted(p["id"] for p in on_gitlab["proposals"]) == ["case:hc-1", "issue:iss-1"]
    assert on_github["proposals"] == []


async def test_the_feed_filters_by_owner_and_kind(app: FastAPI) -> None:
    async with await _client(app) as client:
        mine = (await client.get("/api/v1/proposals", params={"owner": "@Octo"})).json()
        cases = (await client.get("/api/v1/proposals", params={"kind": "case"})).json()
        bad = await client.get("/api/v1/proposals", params={"kind": "merge"})
    assert sorted(p["id"] for p in mine["proposals"]) == ["case:hc-1", "issue:iss-1"]
    assert [p["id"] for p in cases["proposals"]] == ["case:hc-1"]
    assert bad.status_code == 422


async def test_accept_runs_each_kind_through_its_own_path(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    async def accept_case(case_id: str, body: Any, request: Any, cases: Any) -> dict[str, Any]:
        calls.append(("case", f"{case_id} by {body.accepted_by}"))
        return {"job_id": "job-case"}

    async def triage_issue(issue_id: str, request: Any, issues: Any, executor: Any, body: Any = None) -> Any:
        calls.append(("issue", issue_id))
        return SimpleNamespace(job_id="job-issue")

    async def start_fix(request: Any, proposal: Any, run_with: Any = None) -> str:
        calls.append(("finding", proposal.extra["path"]))
        return "job-fix"

    monkeypatch.setattr("embry0.api.v1.cases.accept_case", accept_case)
    monkeypatch.setattr("embry0.api.v1.issues.triage_issue", triage_issue)
    monkeypatch.setattr(proposals_api, "_start_fix", start_fix)
    monkeypatch.setattr(proposals_api, "get_healing_cases_repo", lambda request: None)
    monkeypatch.setattr(proposals_api, "get_issues_repo", lambda request: None)
    monkeypatch.setattr(proposals_api, "get_issue_executor", lambda request: None)

    async with await _client(app) as client:
        finding_id = next(
            p["id"] for p in (await client.get("/api/v1/proposals")).json()["proposals"] if p["kind"] == "finding"
        )
        answers = [
            await client.post(f"/api/v1/proposals/{pid}/accept", json={"by": "octo"})
            for pid in ("case:hc-1", "issue:iss-1", finding_id)
        ]
        after = (await client.get("/api/v1/proposals")).json()
        again = await client.post("/api/v1/proposals/case:hc-1/accept", json={"by": "octo"})
    assert [a.status_code for a in answers] == [202, 202, 202]
    assert [a.json()["job_id"] for a in answers] == ["job-case", "job-issue", "job-fix"]
    assert calls == [("case", "hc-1 by octo"), ("issue", "iss-1"), ("finding", "AGENTS.md")]
    assert after["count"] == 0, "an accepted proposal is not asked again"
    assert again.status_code == 404


async def test_a_failed_start_records_no_answer(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    async def refuse(issue_id: str, request: Any, issues: Any, executor: Any, body: Any = None) -> Any:
        raise HTTPException(status_code=409, detail="Cannot triage issue in 'triaging' state")

    monkeypatch.setattr("embry0.api.v1.issues.triage_issue", refuse)
    for name in ("get_issues_repo", "get_issue_executor"):
        monkeypatch.setattr(proposals_api, name, lambda request: None)
    async with await _client(app) as client:
        response = await client.post("/api/v1/proposals/issue:iss-1/accept", json={"by": "octo"})
    assert response.status_code == 409 and app.state.proposals_repo.recorded == []


async def test_decline_and_snooze_record_who_and_why(app: FastAPI) -> None:
    async with await _client(app) as client:
        declined = await client.post(
            "/api/v1/proposals/case:hc-1/decline", json={"by": "octo", "reason": "known, harmless"}
        )
        past = await client.post(
            "/api/v1/proposals/issue:iss-1/snooze", json={"by": "pat", "until": (T0 - timedelta(days=1)).isoformat()}
        )
        until = (datetime.now(UTC) + timedelta(days=2)).isoformat()
        snoozed = await client.post("/api/v1/proposals/issue:iss-1/snooze", json={"by": "pat", "until": until})
        left = (await client.get("/api/v1/proposals")).json()
    assert declined.status_code == 200 and declined.json()["decision"] == "decline"
    assert past.status_code == 422
    assert snoozed.status_code == 200 and snoozed.json()["until"] is not None
    assert [p["kind"] for p in left["proposals"]] == ["finding"]
    recorded = app.state.proposals_repo.recorded
    assert [(r["proposal_id"], r["decision"], r["decided_by"], r["reason"]) for r in recorded] == [
        ("case:hc-1", "decline", "octo", "known, harmless"),
        ("issue:iss-1", "snooze", "pat", ""),
    ]


async def test_an_answer_must_say_who_gave_it(app: FastAPI) -> None:
    async with await _client(app) as client:
        response = await client.post("/api/v1/proposals/case:hc-1/decline", json={"reason": "no"})
    assert response.status_code == 422


async def test_a_finding_fix_is_an_audit_job_on_its_one_file_that_opens_a_pull_request(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: dict[str, Any] = {}

    async def read_head(client: Any, repo: str) -> tuple[str, str]:
        return "abc123", "tree1"

    async def start_job(context: Any, **kwargs: Any) -> str:
        started.update(context=context, **kwargs)
        return "job-fix"

    monkeypatch.setattr("embry0.services.context_audit.read_head", read_head)
    monkeypatch.setattr("embry0.services.context_audit.start_context_audit_job", start_job)
    refusal: list[Any] = [None]
    app.state.issue_executor = SimpleNamespace(dispatch_refusal=lambda actor: _resolved(refusal[0]))
    app.state.jobs_repo = app.state.templates_repo = object()
    app.state.config = SimpleNamespace(audit_log_path=None)
    # The fix job's hard cap is the budget section's fix_job_usd, as the repository's organization reads it.
    app.state.settings = BudgetStore({"fix_job_usd": 1.5, "context_audit_job_usd": 9.0})

    async with await _client(app) as client:
        finding_id = next(
            p["id"] for p in (await client.get("/api/v1/proposals")).json()["proposals"] if p["kind"] == "finding"
        )
        refusal[0] = SimpleNamespace(public_message="the daily budget is spent")
        refused = await client.post(f"/api/v1/proposals/{finding_id}/accept", json={"by": "docs-owner"})
        refusal[0] = None
        accepted = await client.post(f"/api/v1/proposals/{finding_id}/accept", json={"by": "docs-owner"})
    assert refused.status_code == 429 and "budget" in refused.json()["detail"]
    assert accepted.status_code == 202 and accepted.json()["job_id"] == "job-fix"
    assert started["context"].files == ("AGENTS.md",) and started["context"].head_sha == "abc123"
    assert started["open_pr"] is True and started["dispatched_by"] == "embry0:proposals"
    assert started["budget_usd"] == 1.5, "a finding's fix gets budget.fix_job_usd, not the audit's cap"
    assert started["dispatched_by"] == FIX_ACTOR, "the actor the schedule and the feed read as no audit (#222)"
    assert "The claim at AGENTS.md:3: Run make check" in started["task"]


async def test_a_finding_fix_lists_the_instruction_files_its_repository_reads(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#170: the fix job's command lists the files the repository's settings name, as the schedule's audits do."""
    from embry0.settings.sections import CATALOG
    from embry0.settings.store import build_snapshot

    async def read_head(client: Any, repo: str) -> tuple[str, str]:
        return "abc123", "tree1"

    async def no_audit(*args: Any, **kwargs: Any) -> None:
        return None

    created: list[dict[str, Any]] = []

    class Jobs:
        async def create(self, **kwargs: Any) -> str:
            created.append(kwargs)
            return "job-fix"

    class Templates:
        async def list_all(self) -> list[dict[str, Any]]:
            return [{"name": "context-audit"}]

    class Documents:
        """The deployment's document and the acme organization's layer, which names its own files."""

        async def snapshot(self) -> Any:
            layer = {"context_audit": {"files": ["**/AGENTS.md", "**/CONVENTIONS.md"]}}
            rows = [
                {"scope": "deployment", "data": {}, "generation": 1, "updated_at": None},
                {"scope": "organization:acme", "data": layer, "generation": 1, "updated_at": None},
            ]
            return build_snapshot(CATALOG, rows, token=(), log=False)

    monkeypatch.setattr("embry0.services.context_audit.read_head", read_head)
    monkeypatch.setattr("embry0.services.context_audit.emit_audit", no_audit)
    app.state.issue_executor = SimpleNamespace(
        dispatch_refusal=lambda actor: _resolved(None), submit_job=lambda *args, **kwargs: None
    )
    app.state.jobs_repo, app.state.templates_repo = Jobs(), Templates()
    app.state.settings = Documents()

    async with await _client(app) as client:
        finding_id = next(
            p["id"] for p in (await client.get("/api/v1/proposals")).json()["proposals"] if p["kind"] == "finding"
        )
        accepted = await client.post(f"/api/v1/proposals/{finding_id}/accept", json={"by": "docs-owner"})
    assert accepted.status_code == 202, accepted.text
    task = str(created[0]["task"])
    assert "':(glob)**/CONVENTIONS.md'" in task, "the fix's command does not list the organization's own files"
    assert "':(glob)**/HANDOFF.md'" not in task, "the fix's command lists the built-in files the layer replaced"
    assert task.startswith("Fix one outdated claim"), "the fix's own task stays first"


async def _resolved(value: Any) -> Any:
    return value


# ---- proposal briefs (spec 2026-10-25) ----


class FakeLeads:
    async def open_leads(self) -> list[dict[str, Any]]:
        def ticket(lead_id: str, readiness: int) -> dict[str, Any]:
            return {
                "id": lead_id,
                "kind": "ticket",
                "repo": "acme/api",
                "title": f"Ticket {lead_id}",
                "summary": f"A backlog ticket embry0 could take ({readiness}/100 ready): Ticket {lead_id}",
                "details": "",
                "action": "Put the trigger label on the ticket and start its pipeline",
                "first_seen": T0,
                "data": {"tracker": "linear", "key": lead_id, "readiness": readiness},
            }

        return [ticket("ticket:ready", 70), ticket("ticket:thin", 40)]


class FakeBriefs:
    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows = rows

    async def all(self) -> dict[str, dict[str, Any]]:
        return self.rows


async def test_a_thin_ticket_is_held_out_of_the_chat_feed_but_the_page_can_see_it(app: FastAPI) -> None:
    app.state.leads_repo = FakeLeads()
    async with await _client(app) as client:
        asked = (await client.get("/api/v1/proposals", params={"kind": "ticket"})).json()
        everything = (await client.get("/api/v1/proposals", params={"kind": "ticket", "include_held": "true"})).json()
        thin = (await client.get("/api/v1/proposals/ticket:thin")).json()
    assert [p["id"] for p in asked["proposals"]] == ["ticket:ready"] and asked["count"] == 1
    assert {p["id"]: p["held"] for p in everything["proposals"]} == {"ticket:ready": False, "ticket:thin": True}
    assert thin["held"] is True, "a held proposal is still readable (and answerable) by id"


async def test_the_floor_is_the_settings_document_s(app: FastAPI) -> None:
    app.state.leads_repo = FakeLeads()
    app.state.settings = MemorySettings({"ask_min": 30})
    async with await _client(app) as client:
        asked = (await client.get("/api/v1/proposals", params={"kind": "ticket"})).json()
    assert sorted(p["id"] for p in asked["proposals"]) == ["ticket:ready", "ticket:thin"]


async def test_each_proposal_carries_its_brief_while_it_matches(app: FastAPI) -> None:
    from embry0.services.proposal_briefs import input_hash
    from embry0.services.proposals import case_proposal

    case = case_proposal(
        {
            "id": "hc-1",
            "repo": "acme/api",
            "title": "Timeouts",
            "severity": "high",
            "occurrence_count": 3,
            "created_at": T0,
            "tracked_at": T0,
        }
    )
    row = {
        "input_hash": input_hash(case),
        "title": "Requests to the API time out",
        "problem": "Some requests time out.",
        "fix": "Find the slow call and fix it. You get a pull request.",
        "size": "medium",
        "model": "custom/local",
        "written_at": T0,
    }
    app.state.proposal_briefs_repo = FakeBriefs({"case:hc-1": row})
    async with await _client(app) as client:
        listed = (await client.get("/api/v1/proposals")).json()
        one = (await client.get("/api/v1/proposals/case:hc-1")).json()
    by_id = {p["id"]: p for p in listed["proposals"]}
    assert by_id["case:hc-1"]["brief"]["title"] == "Requests to the API time out"
    assert by_id["issue:iss-1"]["brief"] is None, "not written yet"
    assert one["brief"]["fix"].endswith("You get a pull request.")
    app.state.proposal_briefs_repo = FakeBriefs({"case:hc-1": {**row, "input_hash": "older"}})
    async with await _client(app) as client:
        stale = (await client.get("/api/v1/proposals/case:hc-1")).json()
    assert stale["brief"] is None, "a brief written from an older version is not shown"


async def test_the_feed_without_its_repository_answers_503_and_leaves_no_coroutine_unawaited() -> None:
    """The feed built ``open_proposals``' coroutine before it looked the repository up; the 503 then left that
    coroutine never awaited, a RuntimeWarning each time."""
    import gc
    import warnings

    from fastapi import HTTPException

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(proposals_repo=None)))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(HTTPException) as refused:
            await proposals_api.feed(request)  # type: ignore[arg-type]
        gc.collect()
    assert refused.value.status_code == 503
    unawaited = [str(w.message) for w in caught if "never awaited" in str(w.message)]
    assert not unawaited, f"the 503 left a coroutine behind: {unawaited}"


class _Answering(FakeForge):
    """The forge answers every CODEOWNERS place with ``status`` (the repository itself reads), and keeps each ask."""

    def __init__(self, status: int, asked: list[str]) -> None:
        self.status = status
        self.asked = asked

    async def get(self, path: str, **_: Any) -> Any:
        self.asked.append(path)
        if path == "/repos/acme/docs":
            return SimpleNamespace(status_code=200, json=lambda: {"default_branch": "main"})
        return SimpleNamespace(status_code=self.status, json=lambda: {})


@pytest.mark.parametrize("status", [403, 500, 502])
async def test_a_codeowners_that_could_not_be_read_is_unread_not_nobody_s_and_is_read_again(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """A refused or failed read (a rate limit, a 5xx) is not "no owners": the proposal says its owners are unread,
    so who may decide it is not decided on them, and nothing is cached: the next read asks the forge again."""
    asked: list[str] = []

    async def forge_client(request: Any, org: Any) -> Any:
        return _Answering(status, asked)

    for name in ("org_own_forge_client", "org_forge_client"):
        monkeypatch.setattr(f"embry0.api.v1.organizations.{name}", forge_client)
    async with await _client(app) as client:
        first = (await client.get("/api/v1/proposals")).json()["proposals"]
        reads = len(asked)
        await client.get("/api/v1/proposals")
    docs = next(p for p in first if p["repo"] == "acme/docs")
    assert (docs["owners"], docs.get("owners_unread")) == ([], True), docs
    assert all(not p.get("owners_unread") for p in first if p["repo"] != "acme/docs"), "a preference was read"
    assert reads and len(asked) == 2 * reads, f"a failed read is not cached: {asked}"


async def test_a_codeowners_at_no_place_is_nobody_s_and_is_cached(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    async def forge_client(request: Any, org: Any) -> Any:
        return _Answering(404, asked)

    for name in ("org_own_forge_client", "org_forge_client"):
        monkeypatch.setattr(f"embry0.api.v1.organizations.{name}", forge_client)
    async with await _client(app) as client:
        first = (await client.get("/api/v1/proposals")).json()["proposals"]
        reads = len(asked)
        await client.get("/api/v1/proposals")
    docs = next(p for p in first if p["repo"] == "acme/docs")
    assert (docs["owners"], "owners_unread" in docs) == ([], False), docs
    assert reads and len(asked) == reads, "not there at any place: no owners, read once an hour"


class _RegistryDown(_Registry):
    """The registry: ``failing`` (``registered_as`` or ``forge_or_none``) raises, and every ask is kept."""

    def __init__(self, failing: str, asked: list[str]) -> None:
        super().__init__("github")
        self.failing = failing
        self.asked = asked

    async def registered_as(self, repo: str) -> str | None:
        self.asked.append(f"registered_as {repo}")
        if self.failing == "registered_as":
            raise ConnectionError("the registry could not be read")
        return await super().registered_as(repo)

    async def forge_or_none(self, repo: str) -> str:
        self.asked.append(f"forge_or_none {repo}")
        if self.failing == "forge_or_none":
            raise ConnectionError("the registry could not be read")
        return await super().forge_or_none(repo)


@pytest.mark.parametrize("failing", ["registered_as", "forge_or_none"])
async def test_a_repository_whose_forge_could_not_be_looked_up_is_unread_and_looked_up_again(
    app: FastAPI, failing: str
) -> None:
    """A registry lookup that fails is not "no forge": the repository's owners are unread, not nobody's, and nothing
    is cached, so the next read looks it up again."""
    asked: list[str] = []
    app.state.repos_repo = _RegistryDown(failing, asked)
    async with await _client(app) as client:
        first = (await client.get("/api/v1/proposals")).json()["proposals"]
        looked_up = sum(1 for a in asked if a.endswith("acme/docs"))
        await client.get("/api/v1/proposals")
    docs = next(p for p in first if p["repo"] == "acme/docs")
    assert (docs["owners"], docs.get("owners_unread")) == ([], True), docs
    assert looked_up and sum(1 for a in asked if a.endswith("acme/docs")) > looked_up, f"not looked up again: {asked}"


class _NoOrganizations:
    """No organization embry0 knows."""

    async def get(self, owner: str) -> Any:
        return None

    async def list_all(self) -> list[Any]:
        return []


class _CountingRegistry(_Registry):
    """:class:`_Registry` whose answers for an unregistered repository may name no forge (``forge`` None, no
    configured default), and which keeps each repository it is asked about."""

    def __init__(self, forge: str | None, *, registered: bool, asked: list[str]) -> None:
        super().__init__(forge or "", registered=registered)
        self.none = forge is None
        self.asked = asked

    async def registered_as(self, repo: str) -> str | None:
        self.asked.append(repo)
        return await super().registered_as(repo)

    async def forge_or_none(self, repo: str) -> str | None:  # type: ignore[override]
        self.asked.append(repo)
        return None if self.none else await super().forge_or_none(repo)


@pytest.mark.parametrize(
    ("forge", "registered", "organizations"),
    [
        pytest.param("gitlab", True, "github", id="registered-on-another-forge-than-its-organization"),
        pytest.param("github", True, None, id="its-organization-unknown"),
        pytest.param(None, False, None, id="nothing-says-its-forge"),
    ],
)
async def test_a_repository_whose_codeowners_is_not_read_has_its_owners_unread_not_none(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, forge: str | None, registered: bool, organizations: str | None
) -> None:
    """A repository embry0 does not read CODEOWNERS for (on another forge than its organization's, an organization
    embry0 does not know, a forge nothing names) has owners nobody knows: unread, so only an admin decides its
    proposals, not "nobody owns it". No forge is asked, and the answer is kept as a read's is."""
    asked_forge: list[str] = []

    async def forge_client(request: Any, org: Any) -> Any:
        asked_forge.append(str(org))
        return FakeForge()

    monkeypatch.setattr("embry0.api.v1.organizations.org_own_forge_client", forge_client)
    looked_up: list[str] = []
    app.state.repos_repo = _CountingRegistry(forge, registered=registered, asked=looked_up)
    app.state.organizations_repo = FakeOrgs(forge=organizations) if organizations else _NoOrganizations()
    async with await _client(app) as client:
        first = (await client.get("/api/v1/proposals")).json()["proposals"]
        before = looked_up.count("acme/docs")
        await client.get("/api/v1/proposals")
    docs = next(p for p in first if p["repo"] == "acme/docs")
    assert (docs["owners"], docs.get("owners_unread")) == ([], True), docs
    assert asked_forge == [], f"a forge was asked: {asked_forge}"
    assert before and looked_up.count("acme/docs") == before, "a repository not read is kept as a read is, an hour"


@pytest.mark.parametrize(
    ("kind", "name", "read_again"),
    [
        pytest.param("repo", "acme/docs", True, id="its-registration"),
        pytest.param("organization", "acme", True, id="its-organization-s-registration"),
        pytest.param("repo", "acme/elsewhere", False, id="another-repository-s"),
        pytest.param("organization", "other", False, id="another-organization-s"),
    ],
)
async def test_a_kept_codeowners_is_read_again_once_its_registration_is_written(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, kind: str, name: str, read_again: bool
) -> None:
    """A repository's CODEOWNERS is kept for an hour, but a registration written since (the repository's, or its
    organization's: a fix, or registering it again to read a file just added) drops it at once; another's does
    not."""
    from embry0.storage.repositories import registrations

    asked: list[str] = []

    async def forge_client(request: Any, org: Any) -> Any:
        return _Answering(404, asked)

    monkeypatch.setattr("embry0.api.v1.organizations.org_own_forge_client", forge_client)
    async with await _client(app) as client:
        await client.get("/api/v1/proposals")
        reads = len(asked)
        await client.get("/api/v1/proposals")
        assert reads and len(asked) == reads, "kept: the second read asks nothing"
        registrations.written(kind, name)
        await client.get("/api/v1/proposals")
    assert (len(asked) > reads) is read_again, f"read again after {kind} {name} was written: {asked}"


@pytest.mark.parametrize(
    ("repo_forge", "organization_forge", "said"),
    [
        pytest.param("github", "bitbucket", {"organization_forge": "bitbucket"}, id="its-organization-s-forge"),
        pytest.param("bitbucket", "github", {"repo_forge": "bitbucket"}, id="its-own-forge"),
    ],
)
async def test_a_forge_embry0_does_not_read_codeowners_on_is_logged_as_such(
    app: FastAPI, repo_forge: str, organization_forge: str, said: dict[str, str]
) -> None:
    """Not as an organization embry0 does not know: it knows it, on a forge whose CODEOWNERS it does not read."""
    app.state.repos_repo = _Registry(repo_forge)
    app.state.organizations_repo = FakeOrgs(forge=organization_forge)
    with capture_logs() as logs:
        async with await _client(app) as client:
            docs = next(
                p for p in (await client.get("/api/v1/proposals")).json()["proposals"] if p["repo"] == "acme/docs"
            )
    assert docs.get("owners_unread") is True, docs
    events = [e for e in logs if str(e["event"]).startswith("codeowners_not_read")]
    assert [(e["event"], {k: e[k] for k in said}) for e in events] == [("codeowners_not_read_unsupported_forge", said)]


@pytest.mark.parametrize(("status", "unread"), [("removed", True), ("paused", False), ("active", False)])
async def test_a_removed_organization_s_codeowners_is_not_read_and_a_paused_one_s_is(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, status: str, unread: bool
) -> None:
    """A removed organization is as good as unknown: its repositories' owners are unread, so only an admin decides
    their proposals, and its credential is not used. A paused one is still read."""
    built: list[str] = []

    async def forge_client(request: Any, org: Any) -> Any:
        built.append(org.owner)
        return FakeForge()

    monkeypatch.setattr("embry0.api.v1.organizations.org_own_forge_client", forge_client)
    app.state.organizations_repo = FakeOrgs(status=status)
    with capture_logs() as logs:
        async with await _client(app) as client:
            docs = next(
                p for p in (await client.get("/api/v1/proposals")).json()["proposals"] if p["repo"] == "acme/docs"
            )
    assert bool(docs.get("owners_unread")) is unread, docs
    assert (docs["owners"] == ["docs-owner"]) is not unread, docs
    assert (built == []) is unread, f"the removed organization's credential is not used: {built}"
    said = [e["event"] for e in logs if str(e["event"]).startswith("codeowners_not_read")]
    assert said == (["codeowners_not_read_removed_organization"] if unread else []), said
