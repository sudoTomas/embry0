"""POST /api/v1/cases/{id}/accept (#378) — accepting a watcher finding without
the tracker's label. Runs against the real repositories: the case, the stack
row and the issue row are real; the executor and GitHub are planted."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from embry0.services.healing.service import HealingCaseService, TrackerRef
from embry0.services.watcher_stacks import WatcherStack
from embry0.storage.repositories.watcher_stacks import WatcherStacksRepository

REPO = "acme/backend"
URL = f"https://github.com/{REPO}/issues/397"


class _FakeGitHub:
    def __init__(self, issue: dict[str, Any] | None) -> None:
        self.issue = issue
        self.asked: list[tuple[str, int]] = []

    async def get_issue(self, repo: str, number: int) -> dict[str, Any] | None:
        self.asked.append((repo, number))
        return self.issue


def _issue(state: str = "open") -> dict[str, Any]:
    return {
        "number": 397,
        "title": "[watcher] fix(orchestrator): QA check-run creation fails with 403",
        "body": "## Evidence\n\n`qa_check_run_failed` status=403",
        "html_url": URL,
        "state": state,
        "labels": [],
    }


@pytest.fixture
async def wired(api_client: AsyncClient) -> dict[str, Any]:
    app = api_client.app
    await app.state.db.execute("TRUNCATE TABLE healing_cases, issues, watcher_stacks CASCADE")
    stacks = WatcherStacksRepository(app.state.db)
    await stacks.upsert(WatcherStack(repo=REPO, logql='{compose_project="x"}', tracker="github"))
    app.state.watcher_stacks_repo = stacks
    app.state.github_issues_client = _FakeGitHub(_issue())
    app.state.issue_executor = MagicMock(execute=AsyncMock(return_value="job-1"))
    return {"app": app, "cases": app.state.healing_cases, "stacks": stacks}


async def _tracked_case(cases: HealingCaseService, repo: str = REPO, origin: str = "watcher") -> str:
    case = await cases.open_case(repo=repo, origin=origin, title="a finding", tracker=TrackerRef("github", "397", URL))
    assert case["stage"] == "tracked", case["stage"]
    return str(case["id"])


@pytest.mark.asyncio
async def test_accept_dispatches_the_draft_like_the_label_would(api_client: AsyncClient, wired: dict[str, Any]) -> None:
    case_id = await _tracked_case(wired["cases"])
    r = await api_client.post(f"/api/v1/cases/{case_id}/accept", json={"accepted_by": "Alex"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job_id"] == "job-1" and body["status"] == "dispatched" and body["accepted_by"] == "Alex"

    # The embry0 issue row mirrors the GitHub draft, tracker ref and all —
    # the same row the label webhook would have created.
    issue = await wired["app"].state.issues_repo.get_by_tracker_ref("github", REPO, "397")
    assert issue is not None and issue["id"] == body["issue_id"]
    assert issue["tracker_url"] == URL and issue["status"] == "triaging"
    assert "qa_check_run_failed" in issue["body"]
    wired["app"].state.issue_executor.execute.assert_awaited_once_with(issue["id"], dispatched_by="Alex")
    assert wired["app"].state.github_issues_client.asked == [(REPO, 397)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "body"),
    [
        ("accept", {"accepted_by": "embry0:scout"}),
        ("close", {"closed_by": "embry0:watcher", "resolution_path": "non_code"}),
    ],
)
async def test_a_case_answer_may_not_name_an_embry0_actor(
    api_client: AsyncClient, wired: dict[str, Any], route: str, body: dict[str, str]
) -> None:
    """The name is the dispatcher (#246) and the audit's ``accepted_by``; an embry0 actor's name is not a person's."""
    case_id = await _tracked_case(wired["cases"])
    r = await api_client.post(f"/api/v1/cases/{case_id}/{route}", json=body)
    assert r.status_code == 422, f"{route} naming an embry0 actor is refused, got {r.status_code}: {r.text}"
    wired["app"].state.issue_executor.execute.assert_not_awaited()
    assert (await api_client.get(f"/api/v1/cases/{case_id}")).json()["stage"] == "tracked"


@pytest.mark.asyncio
async def test_unknown_case_is_404(api_client: AsyncClient, wired: dict[str, Any]) -> None:
    assert (await api_client.post("/api/v1/cases/hc-nope/accept", json={})).status_code == 404


@pytest.mark.asyncio
async def test_only_a_watcher_finding_can_be_accepted(api_client: AsyncClient, wired: dict[str, Any]) -> None:
    case_id = await _tracked_case(wired["cases"], origin="alert")
    r = await api_client.post(f"/api/v1/cases/{case_id}/accept", json={})
    assert r.status_code == 409 and "not a watcher finding" in r.json()["detail"]
    wired["app"].state.issue_executor.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_gitlab_draft_keeps_the_label_path(api_client: AsyncClient, wired: dict[str, Any]) -> None:
    """Linear drafts are dispatched here since #460 (their tests are in
    test_cases_accept_linear_api.py); GitLab is the tracker still left out."""
    case = await wired["cases"].open_case(
        repo=REPO,
        origin="watcher",
        title="x",
        tracker=TrackerRef("gitlab", "7", "https://gitlab.example.com/acme/backend/-/issues/7"),
    )
    r = await api_client.post(f"/api/v1/cases/{case['id']}/accept", json={})
    assert r.status_code == 409
    assert r.json()["detail"] == "a gitlab draft is accepted in its tracker: add the trigger label to 7"
    wired["app"].state.issue_executor.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_stack_this_orchestrator_does_not_watch_is_refused(
    api_client: AsyncClient, wired: dict[str, Any]
) -> None:
    """The guarantee behind giving the chat bot's narrow key this route."""
    case_id = await _tracked_case(wired["cases"], repo="acme/other")
    r = await api_client.post(f"/api/v1/cases/{case_id}/accept", json={})
    assert r.status_code == 403 and "not a stack this embry0 watches" in r.json()["detail"]
    wired["app"].state.issue_executor.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_disabled_stack_is_refused_too(api_client: AsyncClient, wired: dict[str, Any]) -> None:
    await wired["stacks"].upsert(
        WatcherStack(repo=REPO, logql='{compose_project="x"}', tracker="github", enabled=False)
    )
    case_id = await _tracked_case(wired["cases"])
    assert (await api_client.post(f"/api/v1/cases/{case_id}/accept", json={})).status_code == 403


@pytest.mark.asyncio
async def test_an_already_accepted_case_is_409(api_client: AsyncClient, wired: dict[str, Any]) -> None:
    case_id = await _tracked_case(wired["cases"])
    job_id = await wired["app"].state.jobs_repo.create(repo=REPO, task="an earlier acceptance")
    await wired["cases"].attach_job(case_id, job_id, event_key=f"job:{job_id}")
    r = await api_client.post(f"/api/v1/cases/{case_id}/accept", json={})
    assert r.status_code == 409 and "already accepted" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_draft_closed_on_github_is_409_and_unreadable_is_502(
    api_client: AsyncClient, wired: dict[str, Any]
) -> None:
    case_id = await _tracked_case(wired["cases"])
    wired["app"].state.github_issues_client = _FakeGitHub(_issue(state="closed"))
    assert (await api_client.post(f"/api/v1/cases/{case_id}/accept", json={})).status_code == 409
    wired["app"].state.github_issues_client = _FakeGitHub(None)
    assert (await api_client.post(f"/api/v1/cases/{case_id}/accept", json={})).status_code == 502
    wired["app"].state.issue_executor.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_accepting_twice_does_not_start_a_second_job(api_client: AsyncClient, wired: dict[str, Any]) -> None:
    """The issue row is in `triaging` after the first accept; only `open`
    dispatches, exactly as a second label would be ignored."""
    case_id = await _tracked_case(wired["cases"])
    assert (await api_client.post(f"/api/v1/cases/{case_id}/accept", json={})).status_code == 202
    # The planted executor never advanced the case, so the case still reads
    # `tracked` — the issue row is what refuses the second dispatch.
    r = await api_client.post(f"/api/v1/cases/{case_id}/accept", json={})
    assert r.status_code == 409 and "already active" in r.json()["detail"]
    assert wired["app"].state.issue_executor.execute.await_count == 1


@pytest.mark.asyncio
async def test_the_acceptance_is_audited(api_client: AsyncClient, wired: dict[str, Any]) -> None:
    case_id = await _tracked_case(wired["cases"])
    await api_client.post(f"/api/v1/cases/{case_id}/accept", json={"accepted_by": "Alex"})
    row = await wired["app"].state.db.fetchrow(
        "SELECT details FROM audit_log WHERE action = 'case_accepted' ORDER BY created_at DESC LIMIT 1"
    )
    assert row is not None
    details = row["details"] if isinstance(row["details"], dict) else __import__("json").loads(row["details"])
    assert details["case_id"] == case_id and details["job_id"] == "job-1" and details["accepted_by"] == "Alex"


# ---- #658: accepting a case a not_healed verdict sent back --------------------


@pytest.fixture
async def dispatching(api_client: AsyncClient) -> dict[str, Any]:
    """Like ``wired``, with the real executor: it creates the job row and
    links it to the case, as production does; only the pipeline run is
    dropped. The draft is closed on GitHub: the failed attempt's merge closed it."""
    app = api_client.app
    await app.state.db.execute("TRUNCATE TABLE healing_cases, issues, jobs, watcher_stacks CASCADE")
    await app.state.db.execute("TRUNCATE TABLE repos CASCADE")
    await app.state.repos_repo.register(REPO, forge="github")
    stacks = WatcherStacksRepository(app.state.db)
    await stacks.upsert(WatcherStack(repo=REPO, logql='{compose_project="x"}', tracker="github"))
    app.state.watcher_stacks_repo = stacks
    app.state.github_issues_client = _FakeGitHub(_issue(state="closed"))
    app.state.issue_executor._track_task = lambda coro, **_: coro.close()
    return {"app": app, "cases": app.state.healing_cases}


async def _reopened_case(app: Any, cases: HealingCaseService, tracker: TrackerRef) -> tuple[str, str, str]:
    """A draft accepted by its job, whose PR merged and deployed and did not
    stop the error, sent back by the confirm sweep's reopen: the rows merge
    sync, the deploy marker and the sweep leave. Returns (case, issue, job)."""
    from datetime import UTC, datetime, timedelta

    case_id = str((await cases.open_case(repo=REPO, origin="watcher", title="a finding", tracker=tracker))["id"])
    issue_id = await app.state.issues_repo.create(
        title="a finding",
        repo=REPO,
        labels=["embry0"],
        created_by="case-accept",
        tracker=tracker.kind,
        tracker_key=tracker.key,
        tracker_url=tracker.url,
    )
    job_id = await app.state.jobs_repo.create(repo=REPO, task="the first fix", issue_id=issue_id)
    await cases.attach_job(case_id, job_id, event_key=f"job:{job_id}")
    pr = f"https://github.com/{REPO}/pull/9"
    await app.state.jobs_repo.update(job_id, status="running")
    await app.state.jobs_repo.update(job_id, status="completed", pr_url=pr)
    await app.state.jobs_repo.update(job_id, status="pr_merged")
    await app.state.issues_repo.update(issue_id, status="closed")
    now = datetime.now(UTC)
    await cases.record_merge(case_id, merge_sha="m1", resolution_path="embry0", pr_url=pr, at=now - timedelta(hours=7))
    await cases.record_deploy(case_id, sha="d1", marker_at=now - timedelta(hours=6))
    await cases.record_verdict(case_id, "not_healed", evidence={"seen_since_deploy": True})
    await cases.reopen(case_id, reason="not_healed after the deploy of d1: retry 1 of 1")
    return case_id, issue_id, job_id


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["github", "gitlab"])
async def test_accepting_a_reopened_case_dispatches_the_retry_on_its_issue(
    api_client: AsyncClient, dispatching: dict[str, Any], kind: str
) -> None:
    """The case links the closed issue of its failed attempt, whose job
    delivered a merged PR, and its draft is closed: everything the first
    acceptance refuses. Accepting it again is the person's "try again", so it
    starts a new job on that issue, for any tracker, and the job's link
    accepts the case."""
    app = dispatching["app"]
    case_id, issue_id, first_job = await _reopened_case(app, dispatching["cases"], TrackerRef(kind, "397", URL))

    r = await api_client.post(f"/api/v1/cases/{case_id}/accept", json={"accepted_by": "Alex"})

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["issue_id"] == issue_id, "the retry must run on the failed attempt's issue"
    assert body["job_id"] and body["job_id"] != first_job
    retry = await app.state.jobs_repo.get(body["job_id"])
    assert retry is not None and retry["issue_id"] == issue_id and retry["dispatched_by"] == "Alex"
    assert (await app.state.issues_repo.get(issue_id))["status"] == "triaging"
    detail = (await api_client.get(f"/api/v1/cases/{case_id}")).json()
    assert detail["stage"] == "accepted", detail["stage"]
    assert [job["job_id"] for job in detail["jobs"]] == [first_job, body["job_id"]]
    # The issue is retried; the draft is not read back.
    assert app.state.github_issues_client.asked == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cancelled", "in_progress"])
async def test_a_reopened_case_whose_issue_is_not_closed_and_not_open_is_409(
    api_client: AsyncClient, dispatching: dict[str, Any], status: str
) -> None:
    """Only the closed issue of a delivered attempt is reopened. A cancelled
    one was stopped by a person; one in any other status has a job on it."""
    app = dispatching["app"]
    case_id, issue_id, first_job = await _reopened_case(app, dispatching["cases"], TrackerRef("github", "397", URL))
    await app.state.db.execute("UPDATE issues SET status = $2 WHERE id = $1", issue_id, status)

    r = await api_client.post(f"/api/v1/cases/{case_id}/accept", json={})

    assert r.status_code == 409 and status in r.json()["detail"], r.text
    rows, _total = await app.state.jobs_repo.list_all(issue_id=issue_id, limit=10, offset=0)
    assert [row["job_id"] for row in rows] == [first_job]
    assert (await app.state.issues_repo.get(issue_id))["status"] == status


@pytest.mark.asyncio
async def test_a_reopened_github_case_is_also_accepted_by_its_label(
    api_client: AsyncClient, dispatching: dict[str, Any]
) -> None:
    """The label path needs nothing new: a person reopens the GitHub issue the
    merged fix closed and labels it again, the webhook dispatches on the same
    issue, and the job's link accepts the case."""
    from embry0.services.github_sync import GitHubSyncService
    from embry0.trackers import TriggerContext

    app = dispatching["app"]
    case_id, issue_id, first_job = await _reopened_case(app, dispatching["cases"], TrackerRef("github", "397", URL))
    waiting = (await api_client.get(f"/api/v1/cases/{case_id}")).json()["stage"]
    assert waiting == "tracked", f"a reopened case is at {waiting!r} before anyone accepted the retry"
    sync = GitHubSyncService(github_token="t")

    async def deliver(action: str, labels: list[str]) -> dict[str, Any]:
        payload = {"repository": {"full_name": REPO}, "issue": {**_issue(), "labels": [{"name": n} for n in labels]}}
        ctx = TriggerContext(
            issues_repo=app.state.issues_repo,
            issue_executor=app.state.issue_executor,
            trigger_labels={"embry0"},
            event_type="issues",
            action=action,
        )
        return await sync.handle_webhook_event(payload, ctx)

    await deliver("reopened", ["embry0"])
    await deliver("unlabeled", [])
    labeled = await deliver("labeled", ["embry0"])

    assert labeled.get("dispatched") is True and labeled.get("issue_id") == issue_id, labeled
    detail = (await api_client.get(f"/api/v1/cases/{case_id}")).json()
    assert detail["stage"] == "accepted", detail["stage"]
    assert len(detail["jobs"]) == 2 and detail["jobs"][0]["job_id"] == first_job
