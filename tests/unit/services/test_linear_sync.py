"""LinearSyncService — trigger decision, dedupe, model labels, write-back (EMB-47)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from embry0.services.linear_sync import (
    LinearSyncService,
    LinearTriggerIssue,
    parse_repo_map,
    parse_repo_stamp,
)
from embry0.trackers import TriggerContext

_ISSUE = LinearTriggerIssue(
    tracker="linear",
    native_id="uuid-1",
    key="EMB-48",
    title="Fix the thing",
    body="It is broken.",
    url="https://linear.app/x/issue/EMB-48",
    labels=["embry0", "grok"],
    project="Acme Quoting",
    team_key="EMB",
    state_type="unstarted",
)


def _ctx(issues_repo, issue_executor, dashboard_base_url=""):
    return TriggerContext(
        issues_repo=issues_repo,
        issue_executor=issue_executor,
        dashboard_base_url=dashboard_base_url,
    )


# When the failed job of the #284 retry tests ended (#462: a relabel counts
# only if Linear's history dates the label after this).
_FAILED = {"finished_at": datetime(2026, 9, 20, 10, 0, tzinfo=UTC)}


def _service(**kw: Any) -> LinearSyncService:
    return LinearSyncService(
        "lin_api_key",
        {"Acme Quoting": "acme/quoting"},
        **kw,
    )


class _FakeIssues:
    def __init__(self, existing: dict[str, Any] | None = None) -> None:
        self._existing = existing
        self.created: dict[str, Any] | None = None
        self.updates: list[dict[str, Any]] = []

    async def get_by_tracker_ref(self, tracker: str, repo: str | None, tracker_key: str) -> dict[str, Any] | None:
        return self._existing

    async def create(self, **kw: Any) -> str:
        self.created = kw
        return "iss-abc"

    async def update(self, issue_id: str, **kw: Any) -> None:
        self.updates.append({"issue_id": issue_id, **kw})


def _payload(action: str = "create") -> dict[str, Any]:
    return {"type": "Issue", "action": action, "data": {"id": "uuid-1", "identifier": "EMB-48"}}


async def test_dispatches_labeled_issue(monkeypatch) -> None:
    svc = _service()
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    posted: list[tuple[str, str]] = []
    monkeypatch.setattr(svc, "post_comment", AsyncMock(side_effect=lambda i, b: posted.append((i, b)) or True))
    issues = _FakeIssues()
    executor = AsyncMock()
    executor.execute = AsyncMock(return_value="job-1")

    result = await svc.handle_webhook_event(_payload(), _ctx(issues, executor, "http://e0:8200"))

    assert result["status"] == "accepted" and result["job_id"] == "job-1"
    assert issues.created is not None
    assert issues.created["repo"] == "acme/quoting"
    assert issues.created["title"] == "[EMB-48] Fix the thing"
    assert issues.created["tracker"] == "linear"
    assert issues.created["tracker_key"] == "EMB-48"
    assert issues.created["tracker_native_id"] == "uuid-1"
    # grok label → per-agent override threaded into execute()
    assert executor.execute.call_args.kwargs["agent_models"]["qa"] == "grok-4.7"
    # dispatch comment posted to the Linear issue
    assert posted and posted[0][0] == "uuid-1" and "job-1" in posted[0][1]
    assert posted[0][1].startswith("**Started work on this ticket**")
    assert "http://e0:8200/jobs/job-1" in posted[0][1]


async def test_redispatch_writes_the_freshly_resolved_repo_to_the_row(monkeypatch) -> None:
    """#357: a corrected `repo:` stamp must reach a ticket embry0 already tracks.

    The job is created from the issue row, so a redispatch that updates title,
    body and labels but not `repo` runs against the repo recorded on FIRST
    dispatch — while the log line and the ticket comment both announce the new
    one. This is what sent the RAV-1466 canary to `acme/quoting`
    twelve days after that repo was renamed.
    """
    svc = _service()
    # The project now stamps the post-rename name; the stamp outranks the map.
    renamed = LinearTriggerIssue(**{**_ISSUE.__dict__, "project_summary": "repo: acme/backend"})
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=renamed))
    posted: list[tuple[str, str]] = []
    monkeypatch.setattr(svc, "post_comment", AsyncMock(side_effect=lambda i, b: posted.append((i, b)) or True))

    # Tracked, open, and its only job failed -> redispatchable on a relabel.
    issues = _FakeIssues(existing={"id": "iss-old", "status": "open", "repo": "acme/quoting"})
    executor = AsyncMock()
    executor.execute = AsyncMock(return_value="job-new")
    ctx = _retry_ctx(issues, executor, [{"job_id": "job-old", "status": "failed", "pr_url": None, **_FAILED}])
    monkeypatch.setattr(svc, "trigger_label_added_since", AsyncMock(return_value=True))

    result = await svc.handle_webhook_event(_relabel_payload(), ctx)

    assert result["action"] == "dispatched"
    # Nothing was created — this is the retry path on the existing row.
    assert issues.created is None
    repo_updates = [u for u in issues.updates if "repo" in u]
    assert repo_updates, "redispatch never wrote the resolved repo to the issue row (#357)"
    assert repo_updates[-1]["repo"] == "acme/backend"
    assert repo_updates[-1]["issue_id"] == "iss-old"
    # The announcement and the row must not disagree.
    assert posted and "acme/backend" in posted[0][1]


async def test_dedupes_already_dispatched(monkeypatch) -> None:
    svc = _service()
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    issues = _FakeIssues(existing={"id": "iss-old"})
    executor = AsyncMock()

    result = await svc.handle_webhook_event(_payload("update"), _ctx(issues, executor))

    assert result == {"status": "ignored", "reason": "already dispatched", "issue_id": "iss-old"}
    executor.execute.assert_not_called()


async def test_ignores_without_trigger_label(monkeypatch) -> None:
    svc = _service()
    unlabeled = LinearTriggerIssue(**{**_ISSUE.__dict__, "labels": ["bug"]})
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=unlabeled))
    issues = _FakeIssues()
    executor = AsyncMock()

    result = await svc.handle_webhook_event(_payload(), _ctx(issues, executor))

    assert result["status"] == "ignored" and "label" in result["reason"]
    assert issues.created is None


async def test_ignores_unmapped_project(monkeypatch) -> None:
    """No repo: stamp on the project AND no map entry → skip, never guess."""
    svc = LinearSyncService("k", {"Other Project": "o/r"})
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    issues = _FakeIssues()

    result = await svc.handle_webhook_event(_payload(), _ctx(issues, AsyncMock()))

    assert result["status"] == "ignored"
    assert "repo: stamp" in result["reason"] and "Acme Quoting" in result["reason"]
    assert issues.created is None


def test_parse_repo_stamp() -> None:
    # Real stamp shape: trailing sentence of the project summary.
    assert parse_repo_stamp("Canonical service catalog, usage dashboards. repo: acme/console") == "acme/console"
    assert parse_repo_stamp("repo: acme/portal") == "acme/portal"
    assert parse_repo_stamp("repo:acme/extension") == "acme/extension"
    # First match wins when a doc mentions several.
    assert parse_repo_stamp("repo: o/first then repo: o/second") == "o/first"
    assert parse_repo_stamp("planning-only project, no stamp") is None
    assert parse_repo_stamp("repo: nameonly") is None
    # A GitLab subgroup path must survive whole — the two-segment pattern
    # truncated it to "acme/group" and dispatched at a project that
    # does not exist.
    assert parse_repo_stamp("repo: acme/group/backend") == "acme/group/backend"
    assert parse_repo_stamp("repo: a/b/c/d") == "a/b/c/d"
    assert parse_repo_stamp("") is None


async def test_stamp_in_summary_wins_over_env_map(monkeypatch) -> None:
    """RAV-1397: the project's repo: stamp outranks the static LINEAR_REPO_MAP."""
    svc = _service()  # map says acme for this project
    stamped = LinearTriggerIssue(
        **{
            **_ISSUE.__dict__,
            "project_id": "proj-uuid",
            "project_summary": "Registry hygiene, KPI dashboards. repo: acme/console",
        }
    )
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=stamped))
    monkeypatch.setattr(svc, "post_comment", AsyncMock(return_value=True))
    issues = _FakeIssues()
    executor = AsyncMock()
    executor.execute = AsyncMock(return_value="job-2")

    result = await svc.handle_webhook_event(_payload(), _ctx(issues, executor))

    assert result["status"] == "accepted"
    assert issues.created is not None and issues.created["repo"] == "acme/console"


async def test_stamp_falls_back_to_project_content(monkeypatch) -> None:
    """Summary without a stamp → the project document (content) is scanned next."""
    svc = LinearSyncService("k", {})  # empty map: only the stamp can resolve
    stamped = LinearTriggerIssue(
        **{
            **_ISSUE.__dict__,
            "project_summary": "Partner portal revamp, customer demo path.",
            "project_content": "## Scope\n\nAll portal work.\n\nrepo: acme/portal",
        }
    )
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=stamped))
    monkeypatch.setattr(svc, "post_comment", AsyncMock(return_value=True))
    issues = _FakeIssues()
    executor = AsyncMock()
    executor.execute = AsyncMock(return_value="job-3")

    result = await svc.handle_webhook_event(_payload(), _ctx(issues, executor))

    assert result["status"] == "accepted"
    assert issues.created is not None and issues.created["repo"] == "acme/portal"


async def test_env_map_resolves_when_project_has_no_stamp(monkeypatch) -> None:
    """Stampless project with a LINEAR_REPO_MAP entry still routes (fallback kept)."""
    svc = _service()
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    monkeypatch.setattr(svc, "post_comment", AsyncMock(return_value=True))
    issues = _FakeIssues()
    executor = AsyncMock()
    executor.execute = AsyncMock(return_value="job-4")

    result = await svc.handle_webhook_event(_payload(), _ctx(issues, executor))

    assert result["status"] == "accepted"
    assert issues.created is not None and issues.created["repo"] == "acme/quoting"


@pytest.mark.asyncio
async def test_fetch_trigger_issue_requests_and_parses_project_fields(monkeypatch) -> None:
    """The issue re-fetch carries the project id/summary/content in one round trip."""
    svc = _service()
    captured: dict[str, Any] = {}

    async def _fake_graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        captured["query"] = query
        return {
            "data": {
                "issue": {
                    "id": "uuid-1",
                    "identifier": "ENG-9000",
                    "title": "t",
                    "description": "d",
                    "url": "https://linear.app/x/ENG-9000",
                    "labels": {"nodes": [{"name": "embry0"}]},
                    "project": {
                        "id": "proj-uuid",
                        "name": "Console / Service-Catalog",
                        "description": "Registry hygiene. repo: acme/console",
                        "content": None,
                    },
                    "team": {"key": "ENG"},
                }
            }
        }

    monkeypatch.setattr(svc, "_graphql", _fake_graphql)
    issue = await svc.fetch_trigger_issue("ENG-9000")

    assert "project { id name description content }" in captured["query"]
    assert issue.project_id == "proj-uuid"
    assert issue.project_summary == "Registry hygiene. repo: acme/console"
    assert issue.project_content == ""  # null content → ""
    assert parse_repo_stamp(issue.project_summary) == "acme/console"


async def test_ignores_non_issue_events() -> None:
    svc = _service()
    result = await svc.handle_webhook_event({"type": "Comment", "action": "create"}, _ctx(_FakeIssues(), AsyncMock()))
    assert result["status"] == "ignored"


# ---- the one Linear GraphQL client (#163: linear_dispatch's own is gone) ----


def _graphql_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_trigger_issue_sends_bare_api_key_header() -> None:
    """Linear personal API keys go bare in Authorization — NO Bearer prefix."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["Authorization"]
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {
                    "issue": {
                        "id": "uuid-700",
                        "identifier": "ENG-700",
                        "title": "Fix widget pricing rounding",
                        "description": None,
                        "url": "https://linear.app/example/issue/ENG-700/fix-widget",
                    }
                }
            },
        )

    svc = LinearSyncService("lin_api_abc123", {}, http_client=_graphql_client(handler))
    issue = await svc.fetch_trigger_issue("ENG-700")

    assert captured["url"] == "https://api.linear.app/graphql"
    assert captured["authorization"] == "lin_api_abc123"
    assert captured["json"]["variables"] == {"id": "ENG-700"}
    assert issue.key == "ENG-700"
    assert issue.body == ""  # null description normalized to empty string


async def test_fetch_trigger_issue_raises_when_issue_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"issue": None}})

    svc = LinearSyncService("k", {}, http_client=_graphql_client(handler))
    with pytest.raises(ValueError, match="ENG-999"):
        await svc.fetch_trigger_issue("ENG-999")


async def test_graphql_raises_on_errors_array() -> None:
    """#163: a 200-with-errors response used to be indistinguishable from a
    missing issue — GraphQL reports failures in-band, so errors[] must raise."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": None, "errors": [{"message": "field 'issue' access denied"}]},
        )

    svc = LinearSyncService("k", {}, http_client=_graphql_client(handler))
    with pytest.raises(ValueError, match="access denied"):
        await svc._graphql("query { issue }", {})


def test_model_override_for_labels() -> None:
    svc = _service()
    assert svc.model_override_for(["bug"]) is None
    override = svc.model_override_for(["Grok", "embry0"])
    # #465: the label names xAI's current model, for every agent it covers.
    assert override == {"triage": "grok-4.7", "developer": "grok-4.7", "review": "grok-4.7", "qa": "grok-4.7"}, (
        f"the grok label must put every agent on grok-4.7, got {override}"
    )


def test_model_labels_only_name_catalogue_models() -> None:
    """A label that names an unlisted model would dispatch jobs priced at $0."""
    from embry0.agents.providers import known_models
    from embry0.services.linear_sync import MODEL_LABELS

    named = {model for per_agent in MODEL_LABELS.values() for model in per_agent.values()}
    assert named <= known_models(), f"MODEL_LABELS names uncatalogued models: {sorted(named - known_models())}"


def test_parse_repo_map() -> None:
    assert parse_repo_map("") == {}
    assert parse_repo_map('{"P": "o/r"}') == {"P": "o/r"}
    with pytest.raises(ValueError):
        parse_repo_map("not-json")
    with pytest.raises(ValueError):
        parse_repo_map('{"P": 1}')


@pytest.mark.asyncio
async def test_create_issue_returns_identifier(monkeypatch) -> None:
    """RAV-657: watcher drafts ride issueCreate — label-free by design."""
    svc = _service()
    captured: dict[str, Any] = {}

    async def _fake_graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        captured["query"] = query
        captured["variables"] = variables
        return {
            "data": {
                "issueCreate": {
                    "success": True,
                    "issue": {"id": "lin-1", "identifier": "ENG-2001", "url": "https://linear.app/x/ENG-2001"},
                }
            }
        }

    monkeypatch.setattr(svc, "_graphql", _fake_graphql)
    issue = await svc.create_issue(team_id="team-1", title="[watcher] t", description="d")
    assert issue == {"id": "lin-1", "identifier": "ENG-2001", "url": "https://linear.app/x/ENG-2001"}
    assert captured["variables"]["input"] == {"teamId": "team-1", "title": "[watcher] t", "description": "d"}
    assert "label" not in captured["query"].lower()


@pytest.mark.asyncio
async def test_create_issue_failure_returns_none(monkeypatch) -> None:
    svc = _service()

    async def _fake_graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        return {"data": {"issueCreate": {"success": False}}}

    monkeypatch.setattr(svc, "_graphql", _fake_graphql)
    assert await svc.create_issue(team_id="t", title="x", description="y") is None


# ---- watcher briefing queries (RAV-1106/1134) -------------------------------


@pytest.mark.asyncio
async def test_list_open_issues_filters_team_project_and_states():
    svc = _service()
    svc._graphql = AsyncMock(
        return_value={
            "data": {
                "issues": {
                    "nodes": [
                        {
                            "identifier": "ENG-42",
                            "title": "Known flake",
                            "state": {"name": "In Progress", "type": "started"},
                        },
                        {"identifier": "ENG-43", "title": None, "state": None},
                    ]
                }
            }
        }
    )
    issues = await svc.list_open_issues("team-uuid", project_id="proj-uuid", limit=5)
    assert issues == [
        {"identifier": "ENG-42", "title": "Known flake", "state": "In Progress"},
        {"identifier": "ENG-43", "title": "", "state": ""},
    ]
    variables = svc._graphql.await_args.args[1]
    assert variables["filter"]["team"] == {"id": {"eq": "team-uuid"}}
    assert variables["filter"]["project"] == {"id": {"eq": "proj-uuid"}}
    assert variables["filter"]["state"] == {"type": {"nin": ["completed", "canceled"]}}
    assert variables["first"] == 5


@pytest.mark.asyncio
async def test_list_open_issues_omits_project_filter_when_unset():
    svc = _service()
    svc._graphql = AsyncMock(return_value={"data": {"issues": {"nodes": []}}})
    await svc.list_open_issues("team-uuid")
    assert "project" not in svc._graphql.await_args.args[1]["filter"]


@pytest.mark.asyncio
async def test_issue_state_types_maps_identifiers_and_parses_numbers():
    svc = _service()
    svc._graphql = AsyncMock(
        return_value={
            "data": {
                "issues": {
                    "nodes": [
                        {"identifier": "ENG-1109", "state": {"type": "started"}},
                        {"identifier": "ENG-1097", "state": {"type": "canceled"}},
                    ]
                }
            }
        }
    )
    states = await svc.issue_state_types("team-uuid", ["ENG-1109", "ENG-1097", "not-an-identifier"])
    assert states == {"ENG-1109": "started", "ENG-1097": "canceled"}
    variables = svc._graphql.await_args.args[1]
    assert variables["filter"]["number"] == {"in": [1109, 1097]}


@pytest.mark.asyncio
async def test_issue_state_types_empty_identifiers_short_circuits():
    svc = _service()
    svc._graphql = AsyncMock()
    assert await svc.issue_state_types("team-uuid", ["garbage"]) == {}
    svc._graphql.assert_not_awaited()


# ---- #284: re-dispatch after a terminal failed job ------------------------


class _FakeJobs:
    def __init__(self, jobs: list[dict[str, Any]]) -> None:
        self._jobs = jobs

    async def list_all(self, **kw: Any) -> tuple[list[dict[str, Any]], int]:
        return list(self._jobs), len(self._jobs)


def _relabel_payload(added: bool = True) -> dict[str, Any]:
    """An Issue update whose label set changed — the trigger label is new when ``added``."""
    return {
        "type": "Issue",
        "action": "update",
        "data": {
            "id": "uuid-1",
            "identifier": "EMB-48",
            "labelIds": ["lbl-embry0", "lbl-bug"],
            "labels": [{"id": "lbl-embry0", "name": "Embry0"}, {"id": "lbl-bug", "name": "bug"}],
        },
        "updatedFrom": {"labelIds": ["lbl-bug"] if added else ["lbl-embry0", "lbl-bug", "lbl-gone"]},
    }


def _retry_ctx(issues: _FakeIssues, executor: Any, jobs: list[dict[str, Any]]) -> TriggerContext:
    return TriggerContext(issues_repo=issues, issue_executor=executor, jobs_repo=_FakeJobs(jobs))


async def test_relabel_after_a_failed_job_dispatches_a_new_job(monkeypatch) -> None:
    svc = _service()
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    monkeypatch.setattr(svc, "post_comment", AsyncMock(return_value=True))
    monkeypatch.setattr(svc, "trigger_label_added_since", AsyncMock(return_value=True))
    issues = _FakeIssues(existing={"id": "iss-old", "status": "open"})
    executor = AsyncMock()
    executor.execute = AsyncMock(return_value="job-2")

    result = await svc.handle_webhook_event(
        _relabel_payload(), _retry_ctx(issues, executor, [{"job_id": "job-1", "status": "failed", **_FAILED}])
    )

    assert result["action"] == "dispatched" and result["job_id"] == "job-2"
    assert result["issue_id"] == "iss-old"
    assert issues.created is None  # the tracked row is reused, never duplicated
    assert {"issue_id": "iss-old", "status": "triaging"} in issues.updates
    assert executor.execute.call_args.args[0] == "iss-old"


async def test_a_redelivered_label_add_after_a_failed_job_is_not_a_retry(monkeypatch) -> None:
    """#462: the payload adds the label, but Linear's history dates that add
    before the job ended — a redelivery, or a late first delivery."""
    svc = _service()
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    monkeypatch.setattr(svc, "post_comment", AsyncMock(return_value=True))
    history = AsyncMock(return_value=False)
    monkeypatch.setattr(svc, "trigger_label_added_since", history)
    issues = _FakeIssues(existing={"id": "iss-old", "status": "open"})
    executor = AsyncMock()

    result = await svc.handle_webhook_event(
        _relabel_payload(), _retry_ctx(issues, executor, [{"job_id": "job-1", "status": "failed", **_FAILED}])
    )

    assert result.get("action") != "dispatched", f"a redelivered label-add was read as a retry: {result}"
    assert result.get("dedupe") == "label_not_reapplied", result
    executor.execute.assert_not_called()
    history.assert_awaited_once_with(_ISSUE.native_id, {"embry0"}, _FAILED["finished_at"])


async def test_a_failed_job_with_no_end_time_is_never_retried_on_a_payload_alone(monkeypatch) -> None:
    svc = _service()
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    monkeypatch.setattr(svc, "post_comment", AsyncMock(return_value=True))
    monkeypatch.setattr(svc, "trigger_label_added_since", AsyncMock(return_value=True))
    executor = AsyncMock()

    result = await svc.handle_webhook_event(
        _relabel_payload(),
        _retry_ctx(
            _FakeIssues(existing={"id": "iss-old", "status": "open"}), executor, [{"job_id": "j", "status": "failed"}]
        ),
    )

    assert result.get("dedupe") == "label_not_reapplied", result
    executor.execute.assert_not_called()


@pytest.mark.parametrize(
    ("jobs", "dedupe"),
    [
        ([{"job_id": "job-1", "status": "running"}], "live_job"),
        ([{"job_id": "job-1", "status": "pending"}], "live_job"),
        ([{"job_id": "job-1", "status": "awaiting_input"}], "live_job"),
        ([{"job_id": "job-1", "status": "completed", "pr_url": "https://github.com/o/r/pull/1"}], "open_pr"),
        ([{"job_id": "job-1", "status": "pr_merged"}], "work_delivered"),
    ],
)
async def test_relabel_still_dedupes_when_not_eligible(monkeypatch, jobs, dedupe) -> None:
    svc = _service()
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    issues = _FakeIssues(existing={"id": "iss-old", "status": "open"})
    executor = AsyncMock()

    result = await svc.handle_webhook_event(_relabel_payload(), _retry_ctx(issues, executor, jobs))

    assert result["status"] == "ignored" and result["reason"] == "already dispatched"
    assert result["dedupe"] == dedupe
    executor.execute.assert_not_called()


async def test_unrelated_update_never_redispatches_a_failed_ticket(monkeypatch) -> None:
    """A title edit or a state move is not a request to try again."""
    svc = _service()
    fetch = AsyncMock(return_value=_ISSUE)
    monkeypatch.setattr(svc, "fetch_trigger_issue", fetch)
    issues = _FakeIssues(existing={"id": "iss-old", "status": "open"})
    executor = AsyncMock()
    failed = [{"job_id": "job-1", "status": "failed"}]

    plain = await svc.handle_webhook_event(_payload("update"), _retry_ctx(issues, executor, failed))
    other_label = await svc.handle_webhook_event(_relabel_payload(added=False), _retry_ctx(issues, executor, failed))

    assert plain["reason"] == other_label["reason"] == "already dispatched"
    executor.execute.assert_not_called()
    fetch.assert_not_called()  # answered from the payload, no Linear round trip


async def test_redispatch_loses_cleanly_to_a_concurrent_status_writer(monkeypatch) -> None:
    from embry0.storage.repositories.jobs import StatusTransitionConflict

    svc = _service()
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    issues = _FakeIssues(existing={"id": "iss-old", "status": "open"})

    async def _conflicting_update(issue_id: str, **kw: Any) -> None:
        if kw.get("status") == "triaging":
            raise StatusTransitionConflict("someone else owns it")

    monkeypatch.setattr(issues, "update", _conflicting_update)
    monkeypatch.setattr(svc, "trigger_label_added_since", AsyncMock(return_value=True))
    executor = AsyncMock()

    result = await svc.handle_webhook_event(
        _relabel_payload(), _retry_ctx(issues, executor, [{"job_id": "job-1", "status": "failed", **_FAILED}])
    )

    assert result["status"] == "ignored" and result["dedupe"] == "concurrent_dispatch"
    executor.execute.assert_not_called()


# ---- #284: a failed dispatch never strands the issue in `triaging` --------


async def test_execute_failure_reverts_the_issue_to_open(monkeypatch) -> None:
    svc = _service()
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    issues = _FakeIssues()
    executor = AsyncMock()
    executor.execute = AsyncMock(side_effect=RuntimeError("sandbox backend down"))

    result = await svc.handle_webhook_event(_payload(), _ctx(issues, executor))

    assert result["action"] == "issue_created_dispatch_failed"
    statuses = [u["status"] for u in issues.updates if "status" in u]
    assert statuses == ["triaging", "open"]


async def test_a_removed_organization_s_repository_is_refused_and_the_ticket_told_why(monkeypatch) -> None:
    """#721: the job store refuses the job; the issue goes back to `open` and the ticket says why, naming the code."""
    from embry0.services import linear_notice_texts as texts
    from embry0.storage.repositories.jobs import OrganizationRemoved

    svc = _service()
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    posted: list[tuple[str, str]] = []
    monkeypatch.setattr(svc, "post_comment", AsyncMock(side_effect=lambda i, b: posted.append((i, b)) or True))
    issues = _FakeIssues()
    executor = AsyncMock()
    executor.execute = AsyncMock(side_effect=OrganizationRemoved("acme"))
    ctx = TriggerContext(issues_repo=issues, issue_executor=executor, dispatch_ledger=_Ledger())

    result = await svc.handle_webhook_event(_payload(), ctx)

    assert (result["status"], result["skip"], result["error_code"]) == (
        "ignored",
        "organization_removed",
        "ERR_ORGANIZATION_REMOVED",
    ), result
    assert [u["status"] for u in issues.updates if "status" in u] == ["triaging", "open"]
    assert posted == [("uuid-1", texts.organization_removed("acme/quoting", "acme", auto_retry=False))], posted
    assert "dispatch_failed" not in str(posted), "not the generic failure"


async def test_a_failed_revert_is_logged_not_raised(monkeypatch) -> None:
    from embry0.storage.repositories.jobs import StatusTransitionConflict

    svc = _service()
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    issues = _FakeIssues()
    real_update = issues.update

    async def _update(issue_id: str, **kw: Any) -> None:
        if kw.get("status") == "open":
            raise StatusTransitionConflict("moved on")
        await real_update(issue_id, **kw)

    monkeypatch.setattr(issues, "update", _update)
    executor = AsyncMock()
    executor.execute = AsyncMock(side_effect=RuntimeError("boom"))

    result = await svc.handle_webhook_event(_payload(), _ctx(issues, executor))

    assert result["action"] == "issue_created_dispatch_failed"


# ---- #284: every non-dispatch reason is stated on the ticket, once --------


class _Ledger:
    def __init__(self) -> None:
        self.claimed: set[tuple[str, str, str]] = set()
        self.failures: dict[str, int] = {}

    async def claim(self, tracker: str, key: str, reason: str) -> bool:
        if (tracker, key, reason) in self.claimed:
            return False
        self.claimed.add((tracker, key, reason))
        return True

    async def release(self, tracker: str, key: str, reason: str) -> None:
        self.claimed.discard((tracker, key, reason))

    async def record_failure(self, tracker: str, key: str) -> int:
        self.failures[key] = self.failures.get(key, 0) + 1
        return self.failures[key]

    async def clear(self, tracker: str, key: str) -> None:
        self.claimed = {c for c in self.claimed if c[1] != key}
        self.failures.pop(key, None)


def _commented(svc: LinearSyncService, monkeypatch) -> list[str]:
    bodies: list[str] = []

    async def _post(issue_id: str, body: str) -> bool:
        assert issue_id == "uuid-1"  # Linear addresses comments by the native UUID
        bodies.append(body)
        return True

    monkeypatch.setattr(svc, "post_comment", _post)
    return bodies


async def test_unmapped_repo_is_commented_once(monkeypatch) -> None:
    svc = LinearSyncService("k", {"Other Project": "o/r"})
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    bodies = _commented(svc, monkeypatch)
    ctx = TriggerContext(issues_repo=_FakeIssues(), issue_executor=AsyncMock(), dispatch_ledger=_Ledger())

    first = await svc.handle_webhook_event(_payload(), ctx)
    await svc.handle_webhook_event(_payload("update"), ctx)

    assert first["status"] == "ignored"
    assert len(bodies) == 1
    assert "Did not start work" in bodies[0] and "repo: owner/name" in bodies[0]


async def test_relabel_dedupe_is_commented_with_its_reason(monkeypatch) -> None:
    svc = _service()
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    bodies = _commented(svc, monkeypatch)
    ctx = TriggerContext(
        issues_repo=_FakeIssues(existing={"id": "iss-old", "status": "triaging"}),
        issue_executor=AsyncMock(),
        jobs_repo=_FakeJobs([{"job_id": "job-1", "status": "running"}]),
        dispatch_ledger=_Ledger(),
    )

    await svc.handle_webhook_event(_relabel_payload(), ctx)
    await svc.handle_webhook_event(_relabel_payload(), ctx)

    assert len(bodies) == 1 and "job-1" in bodies[0] and "running" in bodies[0]


async def test_a_silent_caller_never_comments_a_dedupe(monkeypatch) -> None:
    """The reconciler re-reads tickets that are simply being worked on."""
    svc = _service()
    bodies = _commented(svc, monkeypatch)
    ctx = TriggerContext(
        issues_repo=_FakeIssues(existing={"id": "iss-old", "status": "triaging"}),
        issue_executor=AsyncMock(),
        jobs_repo=_FakeJobs([{"job_id": "job-1", "status": "running"}]),
        dispatch_ledger=_Ledger(),
    )

    result = await svc.dispatch_trigger_issue(
        _ISSUE, ctx, created_by="linear-reconciler", label_reapplied=AsyncMock(return_value=True)
    )

    assert result["dedupe"] == "live_job" and bodies == []


@pytest.mark.parametrize(("limit", "reason"), [("daily", "budget"), ("monthly", "budget"), ("rate_limit", "cap")])
async def test_gate_refusal_is_commented_and_creates_nothing(monkeypatch, limit, reason) -> None:
    from embry0.orchestration.budget_gate import DispatchDecision

    svc = _service()
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    bodies = _commented(svc, monkeypatch)
    issues, ledger = _FakeIssues(), _Ledger()
    executor = AsyncMock()
    executor.dispatch_refusal = AsyncMock(
        return_value=DispatchDecision(allowed=False, limit=limit, message="$101.00 of $100.00 by alice")
    )
    ctx = TriggerContext(issues_repo=issues, issue_executor=executor, dispatch_ledger=ledger, actor="alice")

    result = await svc.handle_webhook_event(_payload(), ctx)
    await svc.handle_webhook_event(_payload(), ctx)

    assert result["status"] == "ignored" and result["limit"] == limit
    assert issues.created is None
    executor.execute.assert_not_called()
    executor.dispatch_refusal.assert_awaited_with(actor="alice")
    assert ("linear", "EMB-48", reason) in ledger.claimed
    assert len(bodies) == 1
    # Figure-free: the ticket may be widely readable (#246).
    assert "$" not in bodies[0] and "alice" not in bodies[0]


async def test_execute_failure_is_commented(monkeypatch) -> None:
    svc = _service()
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    bodies = _commented(svc, monkeypatch)
    executor = AsyncMock()
    executor.execute = AsyncMock(side_effect=RuntimeError("secret-bearing internal detail"))
    ctx = TriggerContext(issues_repo=_FakeIssues(), issue_executor=executor, dispatch_ledger=_Ledger())

    await svc.handle_webhook_event(_payload(), ctx)

    assert len(bodies) == 1 and "Could not start work" in bodies[0]
    assert "secret-bearing" not in bodies[0]


# ---- #284: the reconciler's two reads -------------------------------------


def _node(identifier: str, labels: list[str]) -> dict[str, Any]:
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": "t",
        "description": None,
        "url": f"https://linear.app/x/issue/{identifier}",
        "labels": {"nodes": [{"name": name} for name in labels]},
        "state": {"type": "started"},
        "project": {"id": "p", "name": "Proj", "description": "repo: o/r", "content": None},
        "team": {"key": "ENG"},
    }


async def test_list_trigger_issues_filters_by_label_and_open_state_and_pages() -> None:
    svc = _service()
    svc._graphql = AsyncMock(
        side_effect=[
            {
                "data": {
                    "issues": {
                        "nodes": [_node("ENG-1", ["embry0"])],
                        "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                    }
                }
            },
            {
                "data": {
                    "issues": {
                        "nodes": [_node("ENG-2", ["Auto"])],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            },
        ]
    )

    issues = await svc.list_trigger_issues({"Embry0", "auto"})

    assert [i.key for i in issues] == ["ENG-1", "ENG-2"]
    assert issues[0].native_id == "uuid-ENG-1" and issues[0].project_summary == "repo: o/r"
    first, second = (call.args[1] for call in svc._graphql.await_args_list)
    assert first["filter"]["state"] == {"type": {"in": ["backlog", "started", "unstarted"]}}
    assert first["filter"]["or"] == [
        {"labels": {"some": {"name": {"eqIgnoreCase": "auto"}}}},
        {"labels": {"some": {"name": {"eqIgnoreCase": "embry0"}}}},
    ]
    assert first["after"] is None and second["after"] == "c1"


async def test_list_trigger_issues_without_labels_makes_no_call() -> None:
    svc = _service()
    svc._graphql = AsyncMock()

    assert await svc.list_trigger_issues(set()) == []
    svc._graphql.assert_not_called()


def _history_page(entries: list[tuple[str, list[str]]]) -> dict[str, Any]:
    return {
        "data": {
            "issue": {
                "labels": {"nodes": [{"id": "lbl-e", "name": "Embry0"}, {"id": "lbl-b", "name": "bug"}]},
                "history": {
                    "nodes": [{"createdAt": at, "addedLabelIds": ids} for at, ids in entries],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                },
            }
        }
    }


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([("2026-09-20T12:00:00.000Z", ["lbl-e"])], True),  # re-applied after the failure
        ([("2026-09-20T08:00:00.000Z", ["lbl-e"])], False),  # the original application
        ([("2026-09-20T12:00:00.000Z", ["lbl-b"])], False),  # some other label
        ([("2026-09-20T12:00:00.000Z", None)], False),  # a non-label change
        ([], False),
    ],
)
async def test_trigger_label_added_since(entries, expected) -> None:
    from datetime import UTC, datetime

    svc = _service()
    svc._graphql = AsyncMock(return_value=_history_page(entries))

    added = await svc.trigger_label_added_since("uuid-1", {"embry0"}, datetime(2026, 9, 20, 10, 0, tzinfo=UTC))

    assert added is expected


# ---- review B1: only backlog/unstarted/started tickets dispatch -----------


@pytest.mark.parametrize("state_type", ["duplicate", "triage", "completed", "canceled", ""])
async def test_webhook_never_dispatches_from_a_non_dispatchable_state(monkeypatch, state_type) -> None:
    svc = _service()
    ticket = LinearTriggerIssue(**{**_ISSUE.__dict__, "state_type": state_type})
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=ticket))
    bodies = _commented(svc, monkeypatch)
    issues = _FakeIssues()
    executor = AsyncMock()
    ctx = TriggerContext(issues_repo=issues, issue_executor=executor, dispatch_ledger=_Ledger())

    result = await svc.handle_webhook_event(_payload(), ctx)
    await svc.handle_webhook_event(_payload("update"), ctx)

    assert result["status"] == "ignored" and "state type" in result["reason"]
    assert issues.created is None
    executor.execute.assert_not_called()
    assert len(bodies) == 1 and "workflow state" in bodies[0]  # the create asked; the edit did not


@pytest.mark.parametrize("state_type", ["backlog", "unstarted", "started"])
async def test_webhook_dispatches_from_the_allowlisted_states(monkeypatch, state_type) -> None:
    svc = _service()
    ticket = LinearTriggerIssue(**{**_ISSUE.__dict__, "state_type": state_type})
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=ticket))
    monkeypatch.setattr(svc, "post_comment", AsyncMock(return_value=True))
    executor = AsyncMock()
    executor.execute = AsyncMock(return_value="job-1")

    result = await svc.handle_webhook_event(_payload(), _ctx(_FakeIssues(), executor))

    assert result["action"] == "dispatched"


async def test_fetch_reads_the_workflow_state_type() -> None:
    svc = _service()
    svc._graphql = AsyncMock(
        return_value={"data": {"issue": {**_node("ENG-1", ["embry0"]), "state": {"type": "duplicate"}}}}
    )

    ticket = await svc.fetch_trigger_issue("ENG-1")

    assert ticket.state_type == "duplicate"
    assert "state { type }" in svc._graphql.await_args.args[0]


# ---- review m1/M2: the ledger across dispatch cycles ----------------------


async def test_a_successful_dispatch_clears_the_tickets_notices_and_failures(monkeypatch) -> None:
    """A refusal after the next dispatch is news again, not a repeat."""
    svc = _service()
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    _commented(svc, monkeypatch)
    ledger = _Ledger()
    ledger.claimed = {("linear", "EMB-48", "budget"), ("linear", "OTHER-1", "budget")}
    ledger.failures = {"EMB-48": 3, "OTHER-1": 1}
    executor = AsyncMock()
    executor.execute = AsyncMock(return_value="job-1")

    await svc.handle_webhook_event(
        _payload(), TriggerContext(issues_repo=_FakeIssues(), issue_executor=executor, dispatch_ledger=ledger)
    )

    assert ledger.claimed == {("linear", "OTHER-1", "budget")}
    assert ledger.failures == {"OTHER-1": 1}


async def test_a_dispatch_that_raises_is_counted(monkeypatch) -> None:
    svc = _service()
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    _commented(svc, monkeypatch)
    ledger = _Ledger()
    executor = AsyncMock()
    executor.execute = AsyncMock(side_effect=RuntimeError("boom"))
    ctx = TriggerContext(issues_repo=_FakeIssues(), issue_executor=executor, dispatch_ledger=ledger)

    await svc.handle_webhook_event(_payload(), ctx)
    await svc.handle_webhook_event(_payload(), ctx)

    assert ledger.failures == {"EMB-48": 2}


async def test_unmapped_and_refused_warn_once_then_drop_to_debug(monkeypatch) -> None:
    from structlog.testing import capture_logs

    svc = LinearSyncService("k", {"Other Project": "o/r"})
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    _commented(svc, monkeypatch)
    ctx = TriggerContext(issues_repo=_FakeIssues(), issue_executor=AsyncMock(), dispatch_ledger=_Ledger())

    with capture_logs() as logs:
        for _ in range(3):
            await svc.handle_webhook_event(_payload(), ctx)

    levels = [entry["log_level"] for entry in logs if entry["event"] == "linear_trigger_unmapped"]
    assert levels == ["warning", "debug", "debug"]


async def test_a_retry_delivery_without_updated_from_label_ids_fails_safe(monkeypatch) -> None:
    """`updatedFrom.labelIds` is the only evidence of a relabel; absent or malformed means no retry."""
    svc = _service()
    fetch = AsyncMock(return_value=_ISSUE)
    monkeypatch.setattr(svc, "fetch_trigger_issue", fetch)
    executor = AsyncMock()
    failed = [{"job_id": "job-1", "status": "failed"}]
    shapes: list[dict[str, Any]] = [
        {"updatedFrom": {}},
        {"updatedFrom": None},
        {"updatedFrom": {"labelIds": None}},
        {"updatedFrom": {"updatedAt": "2026-09-20T00:00:00Z"}},
        {},
    ]
    for extra in shapes:
        payload = {**_relabel_payload(), **extra}
        if not extra:
            payload.pop("updatedFrom")
        issues = _FakeIssues(existing={"id": "iss-old", "status": "open"})
        result = await svc.handle_webhook_event(payload, _retry_ctx(issues, executor, failed))
        assert result["reason"] == "already dispatched"

    # Label names missing from the payload: the added id cannot be matched to a trigger.
    nameless = _relabel_payload()
    nameless["data"].pop("labels")
    result = await svc.handle_webhook_event(
        nameless, _retry_ctx(_FakeIssues(existing={"id": "iss-old", "status": "open"}), executor, failed)
    )
    assert result["reason"] == "already dispatched"
    executor.execute.assert_not_called()
    fetch.assert_not_called()


async def test_list_trigger_issues_warns_when_the_page_cap_truncates() -> None:
    from structlog.testing import capture_logs

    svc = _service()
    counter = iter(range(10_000))

    async def _endless(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        nodes = [_node(f"ENG-{next(counter)}", ["embry0"]) for _ in range(variables["first"])]
        return {"data": {"issues": {"nodes": nodes, "pageInfo": {"hasNextPage": True, "endCursor": "more"}}}}

    svc._graphql = _endless  # type: ignore[method-assign]

    with capture_logs() as logs:
        issues = await svc.list_trigger_issues({"embry0"})

    assert len(issues) == 200
    (warning,) = [e for e in logs if e["event"] == "linear_trigger_list_truncated"]
    assert warning["log_level"] == "warning"
    assert (warning["returned"], warning["page_size"], warning["max_pages"]) == (200, 25, 8)


# ---- review nit 1: one place adds the machine attribution ------------------


async def test_every_comment_leaves_with_the_machine_attribution_exactly_once() -> None:
    """Linear posts under the API key owner's name; no caller may forget to say a machine wrote it."""
    from embry0.notifications.tracker_comment import TrackerCommentChannel
    from embry0.trackers import TrackerIssueRef, TrackerRegistry

    svc = _service()
    svc._graphql = AsyncMock(return_value={"data": {"commentCreate": {"success": True}}})
    registry = TrackerRegistry()
    registry.register(svc)
    row = {"id": "iss-1", "tracker": "linear", "tracker_key": "EMB-48", "tracker_native_id": "uuid-1"}

    await svc.post_comment("uuid-1", "**dispatched** — job `job-1`")
    await svc.post_issue_comment(TrackerIssueRef(key="EMB-48", native_id="uuid-1"), "plain lifecycle text")
    await svc.post_comment("uuid-1", "embry0 (automated): already attributed")
    await TrackerCommentChannel(registry, "linear").post_outcome(row, {"job_id": "job-1", "status": "completed"})
    await TrackerCommentChannel(registry, "linear").dispatch(row, [{"job_id": "job-1", "question": "Which DB?"}])

    bodies = [call.args[1]["body"] for call in svc._graphql.await_args_list]
    assert len(bodies) == 5
    for body in bodies:
        assert body.startswith("embry0 (automated): ") and body.count("embry0 (automated):") == 1


# ---- review nits 4/5: what the comments say --------------------------------


@pytest.mark.parametrize("base", ["", "http://localhost:8200", "http://127.0.0.1:8200/", "http://0.0.0.0:8200"])
async def test_dispatched_comment_never_prints_a_loopback_dashboard_link(monkeypatch, base) -> None:
    svc = _service()
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    bodies = _commented(svc, monkeypatch)
    executor = AsyncMock()
    executor.execute = AsyncMock(return_value="job-1")

    await svc.handle_webhook_event(_payload(), _ctx(_FakeIssues(), executor, base))

    assert "localhost" not in bodies[0] and "127.0.0.1" not in bodies[0] and "0.0.0.0" not in bodies[0]
    assert "](" not in bodies[0] and "Progress is on the embry0 dashboard." in bodies[0]


def test_public_dashboard_url_keeps_a_real_host() -> None:
    from embry0.services.linear_sync import public_dashboard_url

    assert public_dashboard_url("https://e0.example.com/") == "https://e0.example.com"


def test_notice_texts_give_the_advice_that_matches_who_retries() -> None:
    """Re-applying a label is wrong advice when embry0 retries on its own, and the only advice when it does not."""
    from embry0.services import linear_notice_texts as texts

    for auto in (texts.gate_refused("daily", auto_retry=True), texts.dispatch_failed(auto_retry=True)):
        assert "You do not need to do anything" in auto and "add it again" not in auto
    for manual in (
        texts.gate_refused("daily", auto_retry=False),
        texts.gate_refused("rate_limit", auto_retry=False),
        texts.dispatch_failed(auto_retry=False),
        texts.unmapped_repo("Proj", auto_retry=False),
    ):
        assert "add it again" in manual
    assert "by itself" in texts.unmapped_repo("Proj", auto_retry=True)
    # The relabel path rides the webhook the reconciler exists to back up — offer the dashboard first.
    attempts = texts.reconciler_ticket_attempts(2)
    assert attempts.index("embry0 dashboard") < attempts.index("add it again")
    every = [
        texts.dispatched("job-1", "o/r"),
        texts.state_not_dispatchable("duplicate"),
        *(
            texts.dedupe(c, job_id="job-1", job_status="running", pr_url="https://x/pull/1", issue_status="closed")
            for c in ("live_job", "open_pr", "work_delivered", "issue_closed")
        ),
        texts.reconciler_dispatch_failures(5),
        attempts,
    ]
    for text in every:
        assert "$" not in text and "budget cap" not in text.lower()


async def test_gate_refusal_wording_follows_auto_retry(monkeypatch) -> None:
    from embry0.orchestration.budget_gate import DispatchDecision

    svc = _service()
    monkeypatch.setattr(svc, "fetch_trigger_issue", AsyncMock(return_value=_ISSUE))
    bodies = _commented(svc, monkeypatch)
    executor = AsyncMock()
    executor.dispatch_refusal = AsyncMock(return_value=DispatchDecision(allowed=False, limit="daily"))

    for auto_retry in (False, True):
        ctx = TriggerContext(
            issues_repo=_FakeIssues(), issue_executor=executor, dispatch_ledger=_Ledger(), auto_retry=auto_retry
        )
        await svc.handle_webhook_event(_payload(), ctx)

    assert "add it again" in bodies[0] and "You do not need to do anything" in bodies[1]


async def test_a_relabel_beyond_the_history_page_cap_is_not_a_retry() -> None:
    """250 newer changes hide the relabel: the answer is NO retry, said at debug."""
    from datetime import UTC, datetime

    from structlog.testing import capture_logs

    svc = _service()
    pages = 0

    async def _history(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        nonlocal pages
        pages += 1
        assert "orderBy: createdAt" in query  # the ordering is stated in the query, not assumed
        # Pages 1-5 hold unrelated changes; the relabel sits on page 6, which is never read.
        entries = [("2026-09-20T12:00:00.000Z", ["lbl-e"] if pages > 5 else ["lbl-b"])] * 50
        body = _history_page(entries)
        body["data"]["issue"]["history"]["pageInfo"] = {"hasNextPage": True, "endCursor": f"c{pages}"}
        return body

    svc._graphql = _history  # type: ignore[method-assign]

    with capture_logs() as logs:
        added = await svc.trigger_label_added_since("uuid-1", {"embry0"}, datetime(2026, 9, 20, 10, 0, tzinfo=UTC))

    assert added is False and pages == 5
    (event,) = [e for e in logs if e["event"] == "linear_label_history_truncated"]
    assert event["log_level"] == "debug" and event["decision"] == "no_retry"


# ---- #460: the trigger label, put on for a person accepting a draft --------


def _label(label_id: str, name: str = "embry0", *, team: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "id": label_id,
        "name": name,
        "isGroup": False,
        "retiredAt": None,
        "team": {"id": team} if team else None,
        **extra,
    }


def _labels_response(nodes: list[dict[str, Any]], team: str = "team-eng") -> dict[str, Any]:
    return {"data": {"issue": {"id": "uuid-1", "team": {"id": team}}, "issueLabels": {"nodes": nodes}}}


@pytest.mark.parametrize(
    ("nodes", "chosen"),
    [
        # The ticket's own team's label beats the workspace's, whatever the order.
        ([_label("lbl-ws"), _label("lbl-team", team="team-eng")], "lbl-team"),
        # A workspace label beats another team's.
        ([_label("lbl-other", team="team-ops"), _label("lbl-ws")], "lbl-ws"),
        # A label group and a retired label cannot be applied to a ticket.
        (
            [_label("lbl-group", isGroup=True), _label("lbl-old", retiredAt="2026-01-01T00:00:00Z"), _label("lbl-ws")],
            "lbl-ws",
        ),
        # Only another team's: offered, and Linear is the judge when it is applied.
        ([_label("lbl-other", team="team-ops")], "lbl-other"),
        # Matching is by normalised name; a stray near-match is not the trigger.
        ([_label("lbl-near", name="embry0-later"), _label("lbl-ws", name="Embry0")], "lbl-ws"),
    ],
)
async def test_find_trigger_label_picks_the_label_that_can_go_on_the_ticket(
    nodes: list[dict[str, Any]], chosen: str
) -> None:
    svc = _service()
    svc._graphql = AsyncMock(return_value=_labels_response(nodes))

    found = await svc.find_trigger_label("uuid-1", {"embry0"})

    assert found is not None and found[0] == chosen
    query, variables = svc._graphql.await_args.args
    assert "issueLabels(filter: $filter" in query
    assert variables == {"id": "uuid-1", "filter": {"or": [{"name": {"eqIgnoreCase": "embry0"}}]}}


async def test_find_trigger_label_is_none_when_linear_has_no_such_label() -> None:
    svc = _service()
    svc._graphql = AsyncMock(return_value=_labels_response([_label("lbl-group", isGroup=True)]))
    assert await svc.find_trigger_label("uuid-1", {"embry0"}) is None
    # No trigger label configured: nothing to look for, and Linear is not asked.
    svc._graphql.reset_mock()
    assert await svc.find_trigger_label("uuid-1", set()) is None
    svc._graphql.assert_not_awaited()


async def test_add_label_sends_the_mutation_and_raises_unless_linear_confirms() -> None:
    svc = _service()
    svc._graphql = AsyncMock(return_value={"data": {"issueAddLabel": {"success": True}}})
    await svc.add_label("uuid-1", "lbl-ws")
    query, variables = svc._graphql.await_args.args
    assert "issueAddLabel(id: $id, labelId: $labelId)" in query
    assert variables == {"id": "uuid-1", "labelId": "lbl-ws"}

    svc._graphql = AsyncMock(return_value={"data": {"issueAddLabel": {"success": False}}})
    with pytest.raises(ValueError, match="did not add the label"):
        await svc.add_label("uuid-1", "lbl-ws")


async def test_remove_label_reports_failure_instead_of_raising() -> None:
    svc = _service()
    svc._graphql = AsyncMock(return_value={"data": {"issueRemoveLabel": {"success": True}}})
    assert await svc.remove_label("uuid-1", "lbl-ws") is True
    query, variables = svc._graphql.await_args.args
    assert "issueRemoveLabel(id: $id, labelId: $labelId)" in query
    assert variables == {"id": "uuid-1", "labelId": "lbl-ws"}

    svc._graphql = AsyncMock(return_value={"data": {"issueRemoveLabel": {"success": False}}})
    assert await svc.remove_label("uuid-1", "lbl-ws") is False
    svc._graphql = AsyncMock(side_effect=httpx.ConnectError("linear is down"))
    assert await svc.remove_label("uuid-1", "lbl-ws") is False
