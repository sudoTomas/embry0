"""RAV-1035 — final-status classification judges the terminal state, not history.

state["errors"] is a reducer-append list: an error from a RECOVERED attempt
(developer retry after max-turns, operator-resumed runs) persists for the
job's whole life. The pilot job-efaa8abbc007 recovered, passed review and
QA, opened a PR — and was still persisted as failed because
``bool(result["errors"])`` was treated as fatal.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from embry0.services.issue_executor import IssueExecutor


def _executor():
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock()
    ex._jobs.update = AsyncMock()
    # RAV-1282: the PR fallback reads repo/task from the job row.
    ex._jobs.get = AsyncMock(return_value=None)
    ex._issues = MagicMock()
    ex._issues.update = AsyncMock()
    ex._issues.update_parent_status = AsyncMock()
    ex._db = None  # _persist_deliverables no-ops; emit_audit patched below
    ex._audit_log_path = None
    ex._database_url = "postgresql://unused"
    ex._webhook_notifier = None
    return ex


async def _run(ex, result):
    with (
        patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()),
        patch("embry0.orchestration.checkpoint.purge_thread", new=AsyncMock()),
    ):
        await ex._handle_workflow_result("iss-1", "job-1", result)
    return ex._jobs.update.await_args.kwargs


def _ok(agent):
    return {"agent_type": agent, "is_error": False, "output": "done"}


def _err(agent):
    return {"agent_type": agent, "is_error": True, "error_message": "boom", "output": ""}


@pytest.mark.asyncio
async def test_recovered_error_still_completes():
    """The pilot shape: attempt-1 developer error survives in state.errors,
    but the retry succeeded, review approved, QA passed, output finalized."""
    ex = _executor()
    kwargs = await _run(
        ex,
        {
            "triage_decision": {"action": "proceed"},
            "current_stage": "output_finalized",
            "errors": ["developer: Reached maximum number of turns (40)"],
            "agent_outputs": [_ok("triage"), _err("developer"), _ok("developer"), _ok("review"), _ok("qa")],
            "pr_url": "https://github.com/o/r/pull/325",
            "result_summary": "review verdict",
            "total_cost_usd": 2.84,
        },
    )
    assert kwargs["status"] == "completed"
    assert kwargs["error_code"] is None
    assert kwargs["result_summary"] == "review verdict"


@pytest.mark.asyncio
async def test_clean_run_completes():
    ex = _executor()
    kwargs = await _run(
        ex,
        {
            "triage_decision": {"action": "proceed"},
            "current_stage": "review_passed",
            "agent_outputs": [_ok("triage"), _ok("developer"), _ok("review")],
        },
    )
    assert kwargs["status"] == "completed"


@pytest.mark.asyncio
async def test_run_ending_in_agent_error_fails():
    ex = _executor()
    kwargs = await _run(
        ex,
        {
            "triage_decision": {"action": "proceed"},
            "current_stage": "research_complete",
            "errors": ["research: boom"],
            "agent_outputs": [_ok("triage"), _err("research")],
        },
    )
    assert kwargs["status"] == "failed"


@pytest.mark.asyncio
async def test_failed_stage_fails_even_without_errors():
    ex = _executor()
    for stage in ("abandoned", "failed", "developer_retry", "research_failed", "generic_agent_failed"):
        kwargs = await _run(
            ex,
            {
                "triage_decision": {"action": "proceed"},
                "current_stage": stage,
                "agent_outputs": [_ok("triage")],
            },
        )
        assert kwargs["status"] == "failed", stage


@pytest.mark.asyncio
async def test_terminal_error_code_fails_despite_clean_last_output():
    """qa_exhausted shape: the QA agent itself ran fine (is_error=False) but
    the failure loop hit its cap and set ERR_QA_FAILURES_UNRESOLVED."""
    ex = _executor()
    kwargs = await _run(
        ex,
        {
            "triage_decision": {"action": "proceed"},
            "current_stage": "review_passed",
            "error_code": "ERR_QA_FAILURES_UNRESOLVED",
            "agent_outputs": [_ok("triage"), _ok("developer"), _ok("review"), _ok("qa")],
        },
    )
    assert kwargs["status"] == "failed"
    assert kwargs["error_code"] == "ERR_QA_FAILURES_UNRESOLVED"


@pytest.mark.asyncio
async def test_errors_without_any_agent_output_fail():
    """Infra failures before any agent ran must still fail the job."""
    ex = _executor()
    kwargs = await _run(
        ex,
        {
            "triage_decision": {"action": "proceed"},
            "current_stage": "initialized",
            "errors": ["sandbox: could not create container"],
            "agent_outputs": [],
        },
    )
    assert kwargs["status"] == "failed"


# ---- RAV-1052: outbound webhook emission on the terminal path ---------------


def _notifier_executor():
    from unittest.mock import MagicMock as _MM

    ex = _executor()
    ex._webhook_notifier = _MM()
    return ex


@pytest.mark.asyncio
async def test_completed_job_emits_webhook():
    ex = _notifier_executor()
    await _run(
        ex,
        {
            "triage_decision": {"action": "proceed"},
            "current_stage": "output_finalized",
            "agent_outputs": [_ok("triage"), _ok("research")],
            "result_summary": "the findings",
            "job_kind": "research",
            "repo": "acme/app",
            "total_cost_usd": 0.42,
        },
    )
    ex._webhook_notifier.emit_soon.assert_called_once()
    event, data = ex._webhook_notifier.emit_soon.call_args.args
    assert event == "job.completed"
    assert data["job_id"] == "job-1"
    assert data["kind"] == "research"
    assert data["repo"] == "acme/app"
    assert data["result_summary"] == "the findings"
    assert data["deliverable_count"] == 0  # _db is None → persist no-ops
    assert data["error_code"] is None


@pytest.mark.asyncio
async def test_failed_job_emits_webhook_with_error():
    ex = _notifier_executor()
    await _run(
        ex,
        {
            "triage_decision": {"action": "proceed"},
            "current_stage": "developer_failed",
            "agent_outputs": [_err("developer")],
            "errors": ["boom"],
            "result_summary": "it broke",
        },
    )
    event, data = ex._webhook_notifier.emit_soon.call_args.args
    assert event == "job.failed"
    assert data["error_code"] is not None
    assert data["error_message"] == "it broke"


@pytest.mark.asyncio
async def test_result_summary_excerpt_is_capped():
    ex = _notifier_executor()
    await _run(
        ex,
        {
            "triage_decision": {"action": "proceed"},
            "current_stage": "output_finalized",
            "agent_outputs": [_ok("research")],
            "result_summary": "x" * 5000,
        },
    )
    _, data = ex._webhook_notifier.emit_soon.call_args.args
    assert len(data["result_summary"]) == 1000


# ---- RAV-1247: orchestrator-side PR fallback on completion ----


@pytest.mark.asyncio
async def test_completed_without_pr_url_triggers_fallback():
    """Branch pushed, no PR reported: the executor opens the PR itself and
    persists the URL."""
    ex = _executor()
    ex._github_token = "tok"
    fallback = AsyncMock(return_value="https://github.com/o/r/pull/42")
    with patch("embry0.services.github_prs.ensure_pr_for_branch", new=fallback):
        kwargs = await _run(
            ex,
            {
                "triage_decision": {"action": "proceed"},
                "current_stage": "review_passed",
                "repo": "o/r",
                "task": "fix the widget\n\ndetails here",
                "issue_number": 12,
                "branch_name": "embry0/iss-abc-fix",
                "agent_outputs": [_ok("triage"), _ok("developer"), _ok("review")],
            },
        )
    assert kwargs["status"] == "completed"
    assert kwargs["pr_url"] == "https://github.com/o/r/pull/42"
    call = fallback.await_args.kwargs
    assert call["repo"] == "o/r"
    assert call["branch"] == "embry0/iss-abc-fix"
    assert call["title"] == "fix the widget"
    assert "Closes #12" in call["body"]


@pytest.mark.asyncio
async def test_fallback_pr_body_includes_the_developer_summary():
    """The developer cannot open the PR. The body a person reads has to be
    the change they described, not a note that they reported no URL."""
    ex = _executor()
    ex._github_token = "tok"
    developer = {
        "agent_type": "developer",
        "is_error": False,
        "output": json.dumps(
            {
                "pr_url": None,
                "summary": "Stop refusing a legitimate charge under the per-mile ceiling.",
                "files_changed": ["RateGuard.java"],
            }
        ),
    }
    fallback = AsyncMock(return_value="https://github.com/o/r/pull/42")
    with patch("embry0.services.github_prs.ensure_pr_for_branch", new=fallback):
        await _run(
            ex,
            {
                "triage_decision": {"action": "proceed"},
                "current_stage": "review_passed",
                "repo": "o/r",
                "task": "fix the widget\n\ndetails here",
                "issue_number": 12,
                "branch_name": "embry0/iss-abc-fix",
                "agent_outputs": [_ok("triage"), developer, _ok("review")],
            },
        )
    body = fallback.await_args.kwargs["body"]
    assert "Stop refusing a legitimate charge under the per-mile ceiling." in body, (
        "the fallback PR body must carry the developer summary"
    )
    assert "Closes #12" in body


@pytest.mark.asyncio
async def test_completed_with_pr_url_skips_fallback():
    ex = _executor()
    fallback = AsyncMock()
    with patch("embry0.services.github_prs.ensure_pr_for_branch", new=fallback):
        kwargs = await _run(
            ex,
            {
                "triage_decision": {"action": "proceed"},
                "current_stage": "review_passed",
                "repo": "o/r",
                "branch_name": "embry0/iss-abc-fix",
                "pr_url": "https://github.com/o/r/pull/1",
                "agent_outputs": [_ok("triage"), _ok("developer"), _ok("review")],
            },
        )
    fallback.assert_not_awaited()
    assert kwargs["pr_url"] == "https://github.com/o/r/pull/1"


@pytest.mark.asyncio
async def test_failed_job_gets_a_fallback_pr_marked_for_a_person():
    """#349: a failed run's pushed branch is not stranded. Its PR is marked
    human-merge-only and says the job did not finish, so nobody takes a
    possibly-broken branch for a clean result."""
    ex = _executor()
    fallback = AsyncMock(return_value="https://github.com/o/r/pull/77")
    with patch("embry0.services.github_prs.ensure_pr_for_branch", new=fallback):
        kwargs = await _run(
            ex,
            {
                "triage_decision": {"action": "proceed"},
                "current_stage": "failed",
                "repo": "o/r",
                "branch_name": "embry0/iss-abc-fix",
                "agent_outputs": [_ok("triage"), _err("developer")],
                "errors": ["developer died"],
            },
        )
    fallback.assert_awaited_once()
    assert fallback.await_args.kwargs["labels"] == ["embry0", "human-merge-only"]
    assert "did not finish" in fallback.await_args.kwargs["body"]
    assert (kwargs["status"], kwargs["pr_url"]) == ("failed", "https://github.com/o/r/pull/77")


@pytest.mark.asyncio
async def test_fallback_failure_is_fail_open():
    """ensure_pr_for_branch returning None leaves the job completed with a
    null pr_url — exactly the pre-fix behavior."""
    ex = _executor()
    ex._github_token = "tok"
    with patch("embry0.services.github_prs.ensure_pr_for_branch", new=AsyncMock(return_value=None)):
        kwargs = await _run(
            ex,
            {
                "triage_decision": {"action": "proceed"},
                "current_stage": "review_passed",
                "repo": "o/r",
                "branch_name": "embry0/iss-abc-fix",
                "agent_outputs": [_ok("triage"), _ok("developer"), _ok("review")],
            },
        )
    assert kwargs["status"] == "completed"
    assert kwargs["pr_url"] is None


@pytest.mark.asyncio
async def test_fallback_fires_with_repo_and_task_from_job_row():
    """RAV-1282 regression (job-2ae81464f3b1): result is the merge of node
    UPDATES — no node echoes repo/task back to state, so the old
    result.get("repo") gate meant the fallback NEVER fired in production.
    The job row is the source of truth."""
    ex = _executor()
    ex._github_token = "tok"
    ex._jobs.get = AsyncMock(
        return_value={
            "job_id": "job-1",
            "repo": "acme/training",
            "task": "Add a short section to README.md\n\nmore detail",
            "issue_number": None,
        }
    )
    fallback = AsyncMock(return_value="https://github.com/acme/training/pull/7")
    with patch("embry0.services.github_prs.ensure_pr_for_branch", new=fallback):
        kwargs = await _run(
            ex,
            {
                # The realistic production shape: no repo/task keys at all.
                "triage_decision": {"action": "proceed"},
                "current_stage": "developer_complete",
                "branch_name": "embry0/unknown-add-a-short",
                "agent_outputs": [_ok("triage"), _ok("developer")],
            },
        )
    assert kwargs["status"] == "completed"
    assert kwargs["pr_url"] == "https://github.com/acme/training/pull/7"
    call = fallback.await_args.kwargs
    assert call["repo"] == "acme/training"
    assert call["title"] == "Add a short section to README.md"
    assert call["branch"] == "embry0/unknown-add-a-short"


@pytest.mark.asyncio
async def test_fallback_skips_when_repo_unknown_everywhere():
    """No repo in result OR on the job row (repo-less jobs) → no PR attempt."""
    ex = _executor()
    ex._github_token = "tok"
    ex._jobs.get = AsyncMock(return_value={"job_id": "job-1", "repo": None, "task": "t"})
    fallback = AsyncMock()
    with patch("embry0.services.github_prs.ensure_pr_for_branch", new=fallback):
        kwargs = await _run(
            ex,
            {
                "triage_decision": {"action": "proceed"},
                "current_stage": "developer_complete",
                "branch_name": "embry0/x",
                "agent_outputs": [_ok("triage"), _ok("developer")],
            },
        )
    fallback.assert_not_awaited()
    assert kwargs["status"] == "completed"
    assert kwargs["pr_url"] is None


@pytest.mark.asyncio
async def test_fallback_survives_job_row_fetch_failure():
    """A jobs.get exception must not kill result handling; with repo present
    in result it still proceeds, else it skips quietly."""
    ex = _executor()
    ex._github_token = "tok"
    ex._jobs.get = AsyncMock(side_effect=RuntimeError("db down"))
    fallback = AsyncMock(return_value="https://github.com/o/r/pull/9")
    with patch("embry0.services.github_prs.ensure_pr_for_branch", new=fallback):
        kwargs = await _run(
            ex,
            {
                "triage_decision": {"action": "proceed"},
                "current_stage": "review_passed",
                "repo": "o/r",
                "task": "fix it",
                "branch_name": "embry0/x",
                "agent_outputs": [_ok("triage"), _ok("developer"), _ok("review")],
            },
        )
    assert kwargs["pr_url"] == "https://github.com/o/r/pull/9"


@pytest.mark.asyncio
async def test_gitlab_job_fallback_body_carries_closes_ref():
    """#213: jobs.issue_number is GitHub-only (#161), so GitLab jobs composed
    a change-request body with no issue reference — merging the MR never
    closed its issue. The number now comes from the issue row's tracker
    columns."""
    ex = _executor()
    ex._github_token = "tok"
    ex._issues.get = AsyncMock(return_value={"id": "iss-1", "tracker": "gitlab", "tracker_key": "22"})
    fallback = AsyncMock(return_value="https://gitlab.example/mr/20")
    with patch("embry0.services.github_prs.ensure_pr_for_branch", new=fallback):
        await _run(
            ex,
            {
                "triage_decision": {"action": "proceed"},
                "current_stage": "review_passed",
                "repo": "acme/products/fbr",
                "task": "docs: add layout section",
                "branch_name": "embry0/iss-1-docs",
                "agent_outputs": [_ok("triage"), _ok("developer")],
            },
        )
    assert "Closes #22" in fallback.await_args.kwargs["body"]


@pytest.mark.asyncio
async def test_linear_job_fallback_body_has_no_forge_closes_ref():
    """Linear issues have no forge-side number — a bogus Closes #N must not
    appear (it would reference an unrelated issue on the repo's forge)."""
    ex = _executor()
    ex._github_token = "tok"
    ex._issues.get = AsyncMock(return_value={"id": "iss-1", "tracker": "linear", "tracker_key": "ENG-9"})
    fallback = AsyncMock(return_value="https://github.com/o/r/pull/9")
    with patch("embry0.services.github_prs.ensure_pr_for_branch", new=fallback):
        await _run(
            ex,
            {
                "triage_decision": {"action": "proceed"},
                "current_stage": "review_passed",
                "repo": "o/r",
                "task": "t",
                "branch_name": "embry0/iss-1-t",
                "agent_outputs": [_ok("triage"), _ok("developer")],
            },
        )
    assert "Closes #" not in fallback.await_args.kwargs["body"]


@pytest.mark.asyncio
async def test_runner_error_code_on_the_terminal_output_classifies_the_job():
    """#304: when no node set a code, the runner's own diagnosis on the LAST
    (errored) output beats ERR_UNKNOWN."""
    ex = _executor()
    stale = {**_err("review"), "error_code": "ERR_SANDBOX_IMAGE_STALE"}
    kwargs = await _run(
        ex,
        {
            "triage_decision": {"action": "proceed"},
            "current_stage": "review_failed",
            "agent_outputs": [_ok("triage"), _ok("developer"), stale],
        },
    )
    assert kwargs["status"] == "failed"
    assert kwargs["error_code"] == "ERR_SANDBOX_IMAGE_STALE"


@pytest.mark.asyncio
async def test_runner_error_code_on_a_recovered_output_is_history():
    ex = _executor()
    stalled = {**_err("developer"), "error_code": "ERR_SANDBOX_EXEC_STALLED"}
    kwargs = await _run(
        ex,
        {
            "triage_decision": {"action": "proceed"},
            "current_stage": "output_finalized",
            "agent_outputs": [_ok("triage"), stalled, _ok("developer"), _ok("review")],
        },
    )
    assert kwargs["status"] == "completed"
    assert kwargs.get("error_code") is None


@pytest.mark.asyncio
async def test_a_state_level_code_outranks_the_runner_code():
    """#304 × #322: a code a node (or the budget path) put on state is the
    job's classification; the runner's code only fills the ERR_UNKNOWN gap."""
    ex = _executor()
    stale = {**_err("review"), "error_code": "ERR_SANDBOX_IMAGE_STALE"}
    kwargs = await _run(
        ex,
        {
            "triage_decision": {"action": "proceed"},
            "current_stage": "review_failed",
            "error_code": "ERR_BUDGET_OVERRUN",
            "agent_outputs": [_ok("triage"), _ok("developer"), stale],
        },
    )
    assert kwargs["error_code"] == "ERR_BUDGET_OVERRUN"


# ---- the watcher's own analysis jobs send no per-job webhook --------------


@pytest.mark.asyncio
async def test_a_watcher_analysis_job_emits_no_webhook():
    """They arrived hourly as a raw-JSON "job completed" nobody could act on;
    the daily digest counts them instead."""
    from embry0.orchestration import dispatch_actors

    ex = _notifier_executor()
    ex._jobs.get = AsyncMock(return_value={"dispatched_by": dispatch_actors.WATCHER, "task": "x"})
    await _run(
        ex,
        {
            "triage_decision": {"action": "proceed"},
            "current_stage": "output_finalized",
            "agent_outputs": [_ok("analysis")],
            "result_summary": "NO_ISSUE",
            "job_kind": "analysis",
        },
    )
    ex._webhook_notifier.emit_soon.assert_not_called()


@pytest.mark.asyncio
async def test_a_human_dispatched_job_still_emits():
    """The filter is on WHO dispatched it, not on the kind — an analysis job a
    person asked for is still news."""
    ex = _notifier_executor()
    ex._jobs.get = AsyncMock(return_value={"dispatched_by": "alex@example.com", "task": "x"})
    await _run(
        ex,
        {
            "triage_decision": {"action": "proceed"},
            "current_stage": "output_finalized",
            "agent_outputs": [_ok("analysis")],
            "result_summary": "found it",
            "job_kind": "analysis",
        },
    )
    ex._webhook_notifier.emit_soon.assert_called_once()


@pytest.mark.asyncio
async def test_a_monorepo_pr_names_its_area_in_its_title_and_labels():
    """Spec 2026-10-25: the PR embry0 opens says which area of a monorepo it
    changes (most of its files), and the job records it for the Jobs pages."""
    import base64

    import httpx

    from embry0.execution.github_client import GitHubClient
    from embry0.services.areas import AreaReader

    ex = _executor()
    ex._github_token = "tok"
    ex._area_reader = AreaReader()
    areas_file = 'areas:\n  - name: Billing\n    paths: ["services/billing/**"]\n'
    asked: list[str] = []

    async def fake_get(self, path, **kwargs):
        asked.append(path)
        if "/contents/" in path:
            return httpx.Response(200, json={"content": base64.b64encode(areas_file.encode()).decode()})
        return httpx.Response(200, json={"files": [{"filename": "services/billing/A.java"}, {"filename": "x.md"}]})

    fallback = AsyncMock(return_value="https://github.com/o/r/pull/43")
    with (
        patch("embry0.services.github_prs.ensure_pr_for_branch", new=fallback),
        patch.object(GitHubClient, "get", new=fake_get),
    ):
        await _run(
            ex,
            {
                "triage_decision": {"action": "proceed"},
                "current_stage": "review_passed",
                "repo": "o/r",
                "task": "fix the widget",
                "base_branch": "main",
                "branch_name": "embry0/iss-abc-fix",
                "agent_outputs": [_ok("triage"), _ok("developer"), _ok("review")],
            },
        )
    call = fallback.await_args.kwargs
    assert call["title"] == "[Billing] fix the widget"
    assert call["labels"] == ["embry0", "area:billing"]
    assert "/repos/o/r/compare/main...embry0%2Fiss-abc-fix" in asked
    ex._jobs.update.assert_any_await("job-1", area="Billing")


@pytest.mark.asyncio
async def test_a_repo_without_areas_opens_its_pr_as_before():
    import httpx

    from embry0.execution.github_client import GitHubClient
    from embry0.services.areas import AreaReader

    ex = _executor()
    ex._github_token = "tok"
    ex._area_reader = AreaReader()

    async def no_file(self, path, **kwargs):
        return httpx.Response(404, json={"message": "Not Found"})

    fallback = AsyncMock(return_value="https://github.com/o/r/pull/44")
    with (
        patch("embry0.services.github_prs.ensure_pr_for_branch", new=fallback),
        patch.object(GitHubClient, "get", new=no_file),
    ):
        await _run(
            ex,
            {
                "triage_decision": {"action": "proceed"},
                "current_stage": "review_passed",
                "repo": "o/r",
                "task": "fix the widget",
                "base_branch": "main",
                "branch_name": "embry0/iss-abc-fix",
                "agent_outputs": [_ok("triage"), _ok("developer"), _ok("review")],
            },
        )
    call = fallback.await_args.kwargs
    assert (call["title"], call["labels"]) == ("fix the widget", ["embry0"])
    assert not [c for c in ex._jobs.update.await_args_list if "area" in c.kwargs]
