"""A per-key repository allowlist, enforced at every route that starts or stops work.

A key minted with ``allowed_repos`` may start, continue, stop or delete work
only on those repositories; a key without one is unaffected. Each entry point is driven
through the real app (``create_app``) with three callers: an unrestricted
key, a key restricted to ``acme/widgets``, and the master key. A route that
acts on one job answers such a key, on another repository's job, as on a job
that does not exist; the refusal is logged all the same.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from embry0.api.deps import bust_api_key_cache
from embry0.api.repo_allowlist import covered_slugs, key_covers, repo_allowed
from embry0.config import Embry0Config
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token

ALLOWED = "acme/widgets"
OTHER = "acme/secret"

OPEN_TOKEN = "emb_" + "o" * 43
LIMITED_TOKEN = "emb_" + "l" * 43
# The relay's form of the merge decision is accepted from the merge-scope key (or an admin),
# so on that route the allowlist guards the relay's own restricted key, not an operate key.
OPEN_MERGE_TOKEN = "emb_" + "p" * 43
LIMITED_MERGE_TOKEN = "emb_" + "m" * 43


def _key(key_id: str, name: str, allowed: tuple[str, ...] | None, scope: str = "operate") -> ApiKeyRow:
    return ApiKeyRow(
        id=key_id,
        user_id=None,
        name=name,
        prefix="emb_xxxxxxxx",
        scope=scope,
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
        allowed_repos=allowed,
    )


KEYS = {
    hash_token(OPEN_TOKEN): _key("key-open0000001", "open-key", None),
    hash_token(LIMITED_TOKEN): _key("key-limited00001", "dispatch-bot", (ALLOWED,)),
    hash_token(OPEN_MERGE_TOKEN): _key("key-openmerge001", "open-key", None, scope="merge"),
    hash_token(LIMITED_MERGE_TOKEN): _key("key-limitedmrg01", "dispatch-bot", (ALLOWED,), scope="merge"),
}


def _token_for(template: str, token: str) -> str:
    """The merge-decision route takes the relay's form from the merge key: same name, same limits, that scope."""
    if "/merge-approvals/" not in template:
        return token
    return {OPEN_TOKEN: OPEN_MERGE_TOKEN, LIMITED_TOKEN: LIMITED_MERGE_TOKEN}.get(token, token)


JOBS = {
    "job-allowed": {"job_id": "job-allowed", "repo": ALLOWED, "status": "paused", "issue_id": None},
    "job-other": {"job_id": "job-other", "repo": OTHER, "status": "paused", "issue_id": None},
}
ISSUES = {
    "iss-allowed": {"id": "iss-allowed", "repo": ALLOWED, "status": "open", "github_sync_enabled": False},
    "iss-other": {"id": "iss-other", "repo": OTHER, "status": "open", "github_sync_enabled": False},
}
CASES = {
    # Not a watcher finding: past the allowlist the route answers 409.
    "hc-allowed": {"id": "hc-allowed", "repo": ALLOWED, "origin": "manual", "tracker": "", "tracker_key": ""},
    "hc-other": {"id": "hc-other", "repo": OTHER, "origin": "manual", "tracker": "", "tracker_key": ""},
}
APPROVALS = {"mrg-allowed": {"id": "mrg-allowed", "repo": ALLOWED}, "mrg-other": {"id": "mrg-other", "repo": OTHER}}


def _issue_row(issue_id: str) -> dict[str, Any] | None:
    base = ISSUES.get(issue_id)
    if base is None:
        return None
    return {
        **base,
        "title": "t",
        "body": "",
        "priority": "medium",
        "labels": [],
        "created_by": "user",
        "parent_issue_id": None,
        "github_synced_at": None,
        "created_at": "2026-10-10T00:00:00Z",
        "updated_at": "2026-10-10T00:00:00Z",
    }


@pytest.fixture
def app() -> Any:
    from embry0.api.app import create_app

    bust_api_key_cache()
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key")
    app = create_app(config)
    state = app.state
    state.db = None
    state.users_repo = None
    state.provider_repo = None

    keys = MagicMock()
    keys.get_by_hash = AsyncMock(side_effect=lambda h: KEYS.get(h))
    keys.touch_last_used = AsyncMock()
    state.api_keys_repo = keys

    jobs = MagicMock()
    jobs.get = AsyncMock(
        side_effect=lambda job_id: JOBS.get(job_id) or ({"job_id": job_id} if job_id == "job-new" else None)
    )
    jobs.create = AsyncMock(return_value="job-new")
    state.jobs_repo = jobs

    executor = MagicMock()
    executor.start_job = AsyncMock(return_value="job-new")
    executor.start_onboard_job = AsyncMock(return_value="job-new")
    executor.execute = AsyncMock(return_value="job-new")
    executor.resume_or_execute = AsyncMock()
    executor.cancel_job = AsyncMock()
    state.issue_executor = executor
    jobs.list_all = AsyncMock(return_value=([], 0))

    issues = MagicMock()
    issues.get = AsyncMock(side_effect=lambda issue_id: _issue_row(issue_id) or _issue_row("iss-allowed"))
    issues.create = AsyncMock(return_value="iss-allowed")
    issues.update = AsyncMock()
    issues.update_parent_status = AsyncMock()
    issues.get_children = AsyncMock(return_value=[])
    state.issues_repo = issues
    state.github_sync = None

    inputs = MagicMock()
    # inp-allowed belongs to job-allowed / iss-allowed, inp-other to the others.
    inputs.get = AsyncMock(
        side_effect=lambda input_id: {
            "id": input_id,
            "issue_id": input_id.replace("inp-", "iss-"),
            "job_id": input_id.replace("inp-", "job-"),
            "asking_node": "triage",
            "question": "q?",
            "importance": "blocking",
            "auto_answer": None,
            "status": "pending",
            "answer": None,
            "answered_by": None,
            "answered_at": None,
            "created_at": "2026-10-10T00:00:00Z",
        }
    )
    inputs.answer = AsyncMock()
    inputs.count_pending_blocking = AsyncMock(return_value=1)
    inputs.count_pending_blocking_for_job = AsyncMock(return_value=1)
    state.inputs_repo = inputs

    cases = MagicMock()
    cases.get = AsyncMock(side_effect=lambda case_id: CASES.get(case_id))
    state.healing_cases_repo = cases
    state.qa_configs_repo = MagicMock()

    approvals = MagicMock()
    approvals.get = AsyncMock(side_effect=lambda approval_id: APPROVALS.get(approval_id))
    approvals.decide = AsyncMock(
        return_value={"outcome": "rejected", "status": "rejected", "dry_run": True, "pr_url": "", "head_sha": ""}
    )
    state.merge_approvals = approvals

    watcher = {name: MagicMock(run_once=AsyncMock(return_value={"ran": name})) for name in (ALLOWED, OTHER)}
    state.watcher_services = watcher
    yield app
    bust_api_key_cache()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"}


def _refused(response: Any) -> bool:
    is_json = response.content and response.headers.get("content-type") == "application/json"
    detail = response.json().get("detail") if is_json else None
    return response.status_code == 403 and isinstance(detail, str) and detail.startswith("repo_not_allowed")


# (method, path, json body for the allowed repo, json body for the other repo)
# For id-addressed routes the id picks the repo, and the body is the same.
_ANSWER = {"answer": "yes"}
_DECIDE = {"decision": "reject", "decided_by": "someone", "chat_id": "chat-1"}
ENTRY_POINTS: list[tuple[str, str, str, Any, Any]] = [
    ("create a job", "POST", "/api/v1/jobs", {"repo": ALLOWED, "task": "t"}, {"repo": OTHER, "task": "t"}),
    (
        "create a job by context",
        "POST",
        "/api/v1/jobs",
        {"context": {"type": "git", "repo": ALLOWED}, "task": "t"},
        {"context": {"type": "git", "repo": OTHER}, "task": "t"},
    ),
    (
        "create a QA job",
        "POST",
        "/api/v1/jobs",
        {"repo": ALLOWED, "pipeline": "qa", "branch": "main"},
        {"repo": OTHER, "pipeline": "qa", "branch": "main"},
    ),
    (
        "create an onboard job",
        "POST",
        "/api/v1/jobs",
        {"repo": ALLOWED, "pipeline": "onboard"},
        {"repo": OTHER, "pipeline": "onboard"},
    ),
    (
        "create an issue",
        "POST",
        "/api/v1/issues",
        {"title": "t", "repo": ALLOWED, "auto_triage": True},
        {"title": "t", "repo": OTHER, "auto_triage": True},
    ),
    ("update an issue", "PUT", "/api/v1/issues/{issue}", {"title": "new"}, {"title": "new"}),
    ("triage an issue", "POST", "/api/v1/issues/{issue}/triage", None, None),
    ("sync an issue", "POST", "/api/v1/issues/{issue}/sync", None, None),
    ("answer on an issue", "POST", "/api/v1/issues/{issue}/inputs/{input}/answer", _ANSWER, _ANSWER),
    # Stopping or destroying a repository's work is work on that repository.
    ("delete an issue", "DELETE", "/api/v1/issues/{issue}", None, None),
    ("accept a finding", "POST", "/api/v1/cases/{case}/accept", {}, {}),
    ("decide a merge", "POST", "/api/v1/merge-approvals/{approval}/decide", _DECIDE, _DECIDE),
    ("run the watcher", "POST", "/api/v1/watcher/run?stack={stack}", None, None),
    ("build a QA image", "POST", "/api/v1/qa/images/build", {"repo": ALLOWED}, {"repo": OTHER}),
    ("bootstrap a repo", "POST", "/api/v1/repos/bootstrap", {"repo": ALLOWED}, {"repo": OTHER}),
]


# The routes that act on one job: to a key whose list does not cover the job's repository, the job answers as one
# that does not exist (embry0.api.job_allowlist), and the refusal is logged and audited all the same.
JOB_ACTIONS: list[tuple[str, str, str, Any, Any]] = [
    (
        "resume a job",
        "POST",
        "/api/v1/jobs/{job}/resume",
        {"choice": "continue_retrying"},
        {"choice": "continue_retrying"},
    ),
    ("answer on a job", "POST", "/api/v1/jobs/{job}/inputs/{input}/answer", _ANSWER, _ANSWER),
    ("cancel a job", "POST", "/api/v1/jobs/{job}/cancel", None, None),
]


def _path(template: str, which: str) -> str:
    return template.format(
        job=f"job-{which}",
        issue=f"iss-{which}",
        input=f"inp-{which}",
        case=f"hc-{which}",
        approval=f"mrg-{which}",
        stack=ALLOWED if which == "allowed" else OTHER,
    )


def _call(app: Any, token: str, method: str, template: str, body: Any, which: str) -> Any:
    token = _token_for(template, token)
    bootstrap_result = MagicMock()
    bootstrap_result.as_dict.return_value = {"ok": True}
    with patch("embry0.api.v1.bootstrap.bootstrap_repo", AsyncMock(return_value=bootstrap_result)):
        return TestClient(app, raise_server_exceptions=False).request(
            method, _path(template, which), json=body, headers=_auth(token)
        )


@pytest.mark.parametrize(("label", "method", "template", "allowed_body", "other_body"), ENTRY_POINTS)
def test_a_restricted_key_is_refused_on_another_repository(app, label, method, template, allowed_body, other_body):
    with capture_logs() as logs:
        response = _call(app, LIMITED_TOKEN, method, template, other_body, "other")
    assert _refused(response), (
        f"{label}: a key limited to {ALLOWED} reached {OTHER}: {response.status_code} {response.text}"
    )
    assert "dispatch-bot" in response.json()["detail"], f"{label}: the refusal must name the key"
    _assert_refusal_logged_and_nothing_done(app, label, logs)


@pytest.mark.parametrize(("label", "method", "template", "allowed_body", "other_body"), JOB_ACTIONS)
def test_a_restricted_key_acting_on_another_repository_s_job_gets_a_missing_job_s_answer(
    app, label, method, template, allowed_body, other_body
):
    with capture_logs() as logs:
        response = _call(app, LIMITED_TOKEN, method, template, other_body, "other")
    missing = _call(app, LIMITED_TOKEN, method, template, other_body, "missing")
    answers = [(r.status_code, r.headers.get("content-type"), r.content) for r in (response, missing)]
    assert answers[0] == answers[1] and response.status_code == 404, (
        f"{label}: a key limited to {ALLOWED} is answered on {OTHER}'s job as on a missing one: {answers}"
    )
    _assert_refusal_logged_and_nothing_done(app, label, logs)


def _assert_refusal_logged_and_nothing_done(app: Any, label: str, logs: list[dict[str, Any]]) -> None:
    refusals = [e for e in logs if e["event"] == "api_key_repo_refused"]
    assert refusals, f"{label}: no api_key_repo_refused log line"
    assert refusals[0]["key_name"] == "dispatch-bot" and refusals[0]["repo"] == OTHER, refusals[0]
    assert LIMITED_TOKEN not in repr(refusals[0]), f"{label}: the log line carries the key's secret"
    state = app.state
    assert not state.issue_executor.start_job.await_count, f"{label}: a QA job started anyway"
    assert not state.issue_executor.execute.await_count, f"{label}: an issue was dispatched anyway"
    assert not state.issue_executor.submit_job.call_count, f"{label}: a job was submitted anyway"
    assert not state.issue_executor.schedule_resume.call_count, f"{label}: a job was resumed anyway"
    assert not state.issue_executor.cancel_job.await_count, f"{label}: a job was cancelled anyway"
    assert not state.issues_repo.update.await_count, f"{label}: an issue was changed anyway"
    assert not state.issues_repo.create.await_count, f"{label}: an issue was created anyway"
    assert not state.merge_approvals.decide.await_count, f"{label}: a merge was decided anyway"
    assert not state.watcher_services[OTHER].run_once.await_count, f"{label}: the watcher ran anyway"


@pytest.mark.parametrize(("label", "method", "template", "allowed_body", "other_body"), ENTRY_POINTS + JOB_ACTIONS)
def test_a_restricted_key_reaches_its_listed_repository(app, label, method, template, allowed_body, other_body):
    response = _call(app, LIMITED_TOKEN, method, template, allowed_body, "allowed")
    assert not _refused(response), f"{label}: a key limited to {ALLOWED} was refused on it: {response.text}"
    assert response.status_code != 401, f"{label}: the key did not authenticate"
    assert response.status_code != 500, f"{label}: the handler failed past the check: {response.text}"


@pytest.mark.parametrize("token", [OPEN_TOKEN, "master-key"], ids=["unrestricted-key", "master-key"])
@pytest.mark.parametrize(("label", "method", "template", "allowed_body", "other_body"), ENTRY_POINTS + JOB_ACTIONS)
def test_an_unrestricted_caller_is_unaffected(app, token, label, method, template, allowed_body, other_body):
    response = _call(app, token, method, template, other_body, "other")
    assert not _refused(response), f"{label}: an unrestricted caller was refused: {response.text}"
    assert response.status_code != 401, f"{label}: the caller did not authenticate"
    assert response.status_code != 500, f"{label}: the handler failed past the check: {response.text}"


def test_a_restricted_key_cannot_start_a_job_that_names_no_repository(app) -> None:
    response = _call(app, LIMITED_TOKEN, "POST", "/api/v1/jobs", {"task": "t"}, "other")
    assert _refused(response), f"a repo-less job was started by a restricted key: {response.text}"


def test_a_restricted_key_cannot_move_its_issue_to_another_repository(app) -> None:
    response = _call(app, LIMITED_TOKEN, "PUT", "/api/v1/issues/{issue}", {"repo": OTHER}, "allowed")
    assert _refused(response), f"an issue was moved out of the allowlist: {response.text}"
    assert not app.state.issues_repo.update.await_count, "the issue was updated anyway"


def test_a_restricted_key_cannot_delete_its_issue_with_a_child_in_another_repository(app) -> None:
    child = {**_issue_row("iss-other"), "id": "iss-child", "parent_issue_id": "iss-allowed"}
    app.state.issues_repo.get_children = AsyncMock(return_value=[child])
    response = _call(app, LIMITED_TOKEN, "DELETE", "/api/v1/issues/{issue}", None, "allowed")
    assert _refused(response), f"a child issue on {OTHER} was cancelled through its parent: {response.text}"
    assert not app.state.issues_repo.update.await_count, "an issue was cancelled anyway"


def test_a_restricted_key_runs_the_only_watcher_only_when_it_is_listed(app) -> None:
    app.state.watcher_services = {OTHER: MagicMock(run_once=AsyncMock(return_value={}))}
    response = _call(app, LIMITED_TOKEN, "POST", "/api/v1/watcher/run", None, "other")
    assert _refused(response), f"the unnamed single watcher ran for a restricted key: {response.text}"


def test_the_refusal_is_audited(app) -> None:
    with patch("embry0.api.repo_allowlist.emit_audit", AsyncMock()) as audit:
        _call(app, LIMITED_TOKEN, "POST", "/api/v1/jobs", {"repo": OTHER, "task": "t"}, "other")
    assert audit.await_count == 1, "a refusal must write an audit row"
    assert audit.await_args.args[1] == "apikey.repo_refused"
    assert audit.await_args.kwargs["details"]["repo"] == OTHER


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def test_none_is_unrestricted() -> None:
    assert repo_allowed(None, OTHER) and repo_allowed(None, None)


def test_matching_ignores_case() -> None:
    assert repo_allowed(("Acme/Widgets",), "acme/widgets")


def test_a_restricted_key_never_matches_a_missing_repository() -> None:
    assert not repo_allowed((ALLOWED,), None)
    assert not repo_allowed((ALLOWED,), "")


def test_a_prefix_is_not_a_match() -> None:
    assert not repo_allowed((ALLOWED,), "acme/widgets-private")
    assert not repo_allowed(("acme/widgets-private",), ALLOWED)


def test_renames_match_in_both_directions() -> None:
    renames = {"acme/old-widgets": ALLOWED}
    with patch("embry0.storage.repo_renames.REPO_RENAMES", renames):
        assert repo_allowed(("acme/old-widgets",), ALLOWED), "an allowlist naming the old slug must match the new one"
        assert repo_allowed((ALLOWED,), "acme/old-widgets"), "work addressed by the old slug must match the new one"
        assert not repo_allowed(("acme/old-widgets",), OTHER)


def test_a_rename_reaches_the_route(app) -> None:
    renames = {"acme/old-widgets": ALLOWED}
    with patch("embry0.storage.repo_renames.REPO_RENAMES", renames):
        response = _call(app, LIMITED_TOKEN, "POST", "/api/v1/jobs", {"repo": "acme/old-widgets", "task": "t"}, "x")
    assert not _refused(response), f"a job addressed by the old slug was refused: {response.text}"


@pytest.mark.parametrize("allowlist", [(ALLOWED,), ("acme/old-widgets",), ("ACME/Widgets",)])
def test_a_query_narrowed_to_covered_slugs_selects_exactly_what_key_covers_passes(allowlist: tuple[str, ...]) -> None:
    """A list that pages in SQL narrows its query to ``covered_slugs``: it must select every row ``key_covers``
    passes, under the old slug too, and no other."""
    renames = {"acme/old-widgets": ALLOWED}
    request = MagicMock()
    request.state.principal = MagicMock(allowed_repos=allowlist)
    stored = [ALLOWED, ALLOWED.upper(), "acme/old-widgets", "Acme/Old-Widgets", OTHER, "acme/widgets-private", ""]
    with patch("embry0.storage.repo_renames.REPO_RENAMES", renames):
        slugs = covered_slugs(request)
        assert slugs is not None
        selected = [s for s in stored if s.lower() in slugs]
        passed = [s for s in stored if key_covers(request, s)]
    assert selected == passed == [ALLOWED, ALLOWED.upper(), "acme/old-widgets", "Acme/Old-Widgets"], (
        f"an allowlist of {allowlist}: the query selects {selected}, key_covers passes {passed}"
    )


def test_a_key_without_an_allowlist_narrows_nothing_and_an_empty_one_everything() -> None:
    request = MagicMock()
    request.state.principal = MagicMock(allowed_repos=None)
    assert covered_slugs(request) is None and key_covers(request, OTHER)
    request.state.principal = MagicMock(allowed_repos=[])
    assert covered_slugs(request) == [] and not key_covers(request, OTHER)
    request.state.principal = None
    assert covered_slugs(request) is None and key_covers(request, OTHER), "a session or the master key"
