"""RAV-1035 — final-status classification judges the terminal state, not history.

state["errors"] is a reducer-append list: an error from a RECOVERED attempt
(developer retry after max-turns, operator-resumed runs) persists for the
job's whole life. The pilot job-efaa8abbc007 recovered, passed review and
QA, opened a PR — and was still persisted as failed because
``bool(result["errors"])`` was treated as fatal.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from embry0.services.issue_executor import IssueExecutor


def _executor():
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock()
    ex._jobs.update = AsyncMock()
    ex._issues = MagicMock()
    ex._issues.update = AsyncMock()
    ex._issues.update_parent_status = AsyncMock()
    ex._db = None  # _persist_deliverables no-ops; emit_audit patched below
    ex._audit_log_path = None
    ex._database_url = "postgresql://unused"
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
