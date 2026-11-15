"""A job that fails after pushing its branch still gets its pull request (#349).

The orchestrator's PR fallback (RAV-1247) ran only for completed jobs and for
a change the verification gate blocked (#299). Every other job that pushed a
branch and then failed left the work stranded where nobody looks: the audit
behind #349 found six of seven ERR_QA_FAILURES_UNRESOLVED jobs like that, and
a run that RAISED (a hard budget stop, a sandbox that is gone) skipped the
fallback entirely. Now both terminal paths open the PR, say plainly why the
job stopped, and mark it ``human-merge-only``. An abandoned job still opens
nothing: a person said stop (#410).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from embry0.orchestration.budget import BudgetOverrunError
from embry0.safety.error_codes import ErrorCode
from embry0.services.issue_executor import IssueExecutor

PR = "https://github.com/acme/app/pull/9"
BRANCH = "embry0/iss-1-fix-the-idle-timer"


def _executor() -> IssueExecutor:
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock()
    ex._jobs.update = AsyncMock()
    ex._jobs.get = AsyncMock(return_value={"repo": "acme/app", "task": "Fix the idle timer", "issue_number": 7})
    ex._issues = MagicMock()
    # #521: a job that ends reads its issue before putting it back to open.
    ex._issues.get = AsyncMock(return_value={"id": "iss-1", "status": "in_progress"})
    ex._issues.update = AsyncMock()
    ex._issues.update_parent_status = AsyncMock()
    ex._traces = MagicMock()
    ex._traces.total_cost_for_job = AsyncMock(return_value=0.0)
    ex._sandbox = None
    ex._db = None
    ex._audit_log_path = None
    ex._database_url = "postgresql://unused"
    ex._webhook_notifier = None
    ex._github_token = "t"
    ex._repo_prefs = None
    ex._config = SimpleNamespace(environment_secret_key="k", pr_dedup_enabled=False)
    ex._env_repo = None
    ex._budget_gate = None
    ex._emit_audit = AsyncMock()
    ex._post_tracker_outcome = AsyncMock()
    return ex


def _patches(opened: AsyncMock):
    return (
        patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()),
        patch("embry0.orchestration.checkpoint.purge_thread", new=AsyncMock()),
        patch("embry0.services._executor_outcomes._open_change_request", new=opened),
        patch("embry0.services._executor_outcomes._base_branch_for_change_request", new=AsyncMock(return_value="main")),
    )


async def _result_outcome(result: dict[str, Any]) -> tuple[IssueExecutor, AsyncMock]:
    ex = _executor()
    opened = AsyncMock(return_value=PR)
    a, b, c, d = _patches(opened)
    with a, b, c, d:
        await ex._handle_workflow_result("iss-1", "job-1", result)
    return ex, opened


def _failed(error_code: str, **extra: Any) -> dict[str, Any]:
    return {
        "triage_decision": {"action": "proceed"},
        "current_stage": "qa_failed",
        "error_code": error_code,
        "branch_name": BRANCH,
        "developer_summary": "Stop the idle timer from resetting on a timeout.",
        "agent_outputs": [{"agent_type": "qa", "is_error": False, "output": "2 failures"}],
        **extra,
    }


def _terminal(ex: IssueExecutor) -> dict[str, Any]:
    return [c.kwargs for c in ex._jobs.update.await_args_list if c.kwargs.get("status")][-1]


# --- a failed workflow result ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_job_that_failed_after_pushing_gets_its_pull_request() -> None:
    ex, opened = await _result_outcome(_failed(ErrorCode.QA_FAILURES_UNRESOLVED.value))

    assert opened.await_count == 1, "a failed job with a pushed branch must not strand its work (#349)"
    kwargs = opened.await_args.kwargs
    assert kwargs["branch"] == BRANCH
    assert kwargs["labels"] == ["embry0", "human-merge-only"], (
        f"a failed job's PR must say a person merges it: {kwargs}"
    )
    terminal = _terminal(ex)
    assert (terminal["status"], terminal["pr_url"]) == ("failed", PR), terminal


@pytest.mark.asyncio
async def test_the_pr_body_says_plainly_why_the_job_stopped() -> None:
    _, opened = await _result_outcome(_failed(ErrorCode.QA_FAILURES_UNRESOLVED.value))

    body = opened.await_args.kwargs["body"]
    assert "embry0 did not finish this job" in body and "did not pass review or QA" in body, body
    assert "Stop the idle timer from resetting on a timeout." in body, "the developer's summary still belongs in it"
    assert "Closes #7" in body
    for forbidden in ("ERR_", "Traceback", "/workspace"):
        assert forbidden not in body, f"{forbidden!r} is not for a PR reader:\n{body}"


@pytest.mark.asyncio
async def test_an_account_failure_is_named_as_the_accounts() -> None:
    _, opened = await _result_outcome(_failed(ErrorCode.PROVIDER_QUOTA_EXHAUSTED.value))

    body = opened.await_args.kwargs["body"]
    assert "LLM account" in body, f"a spent account is not an operator's configuration mistake:\n{body}"


@pytest.mark.asyncio
async def test_an_abandoned_job_opens_nothing() -> None:
    ex, opened = await _result_outcome(_failed(ErrorCode.ABANDONED.value, current_stage="abandoned"))

    opened.assert_not_awaited()
    assert _terminal(ex).get("pr_url") is None


@pytest.mark.asyncio
async def test_a_failed_job_that_never_named_a_branch_opens_nothing() -> None:
    result = _failed(ErrorCode.QA_FAILURES_UNRESOLVED.value)
    del result["branch_name"]

    _, opened = await _result_outcome(result)

    opened.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_completed_jobs_pr_is_not_marked_as_unfinished() -> None:
    result = {
        "triage_decision": {"action": "proceed"},
        "current_stage": "review_complete",
        "branch_name": BRANCH,
        "agent_outputs": [{"agent_type": "review", "is_error": False, "output": '{"decision": "approved"}'}],
    }

    _, opened = await _result_outcome(result)

    kwargs = opened.await_args.kwargs
    assert kwargs["labels"] == ["embry0"]
    assert "did not finish" not in kwargs["body"]


# --- a run that raised ------------------------------------------------------------------


def _raising_stream(node_outputs: dict[str, Any], exc: BaseException):
    async def _stream(input_value, issue_id, job_id, state_sink=None, **kw):
        if state_sink is not None:
            state_sink.update(node_outputs)
        raise exc

    return _stream


async def _raised(node_outputs: dict[str, Any]) -> tuple[IssueExecutor, AsyncMock]:
    ex = _executor()
    ex._jobs.get = AsyncMock(return_value={"trace_id": None, "repo": "acme/app", "task": "Fix the idle timer"})
    ex._execute_workflow_stream = _raising_stream(node_outputs, BudgetOverrunError(12.5, 10.0))  # type: ignore[assignment]
    opened = AsyncMock(return_value=PR)
    a, b, c, d = _patches(opened)
    with a, b, c, d:
        await ex._run_workflow("iss-1", "job-1", {"title": "Fix the idle timer", "body": "", "repo": "acme/app"})
    return ex, opened


@pytest.mark.asyncio
async def test_a_run_that_raised_after_the_push_still_gets_its_pull_request() -> None:
    ex, opened = await _raised({"branch_name": BRANCH, "base_branch": "main", "current_stage": "developer_complete"})

    assert opened.await_count == 1, "a hard budget stop after the push must not strand the work"
    kwargs = opened.await_args.kwargs
    assert kwargs["labels"] == ["embry0", "human-merge-only"]
    assert "a spending limit stopped it" in kwargs["body"], kwargs["body"]
    terminal = _terminal(ex)
    assert (terminal["status"], terminal.get("pr_url")) == ("failed", PR), terminal


@pytest.mark.asyncio
async def test_a_run_that_raised_before_any_branch_writes_no_pr_url() -> None:
    ex, opened = await _raised({"current_stage": "triage"})

    opened.assert_not_awaited()
    assert "pr_url" not in _terminal(ex), "a failure path must never write a NULL over the job's PR column"
