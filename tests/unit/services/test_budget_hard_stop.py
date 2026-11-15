"""A hard budget stop must still clean up and still report what it spent (#246).

``overrun_mode=hard`` raises ``BudgetOverrunError`` from inside the agent node,
so the graph never returns a final state. Before this, that meant
``_cleanup_sandbox(None, ...)`` destroyed nothing (a container leaked on every
hard stop) and the failure path wrote no cost at all — the job row, ``/stats``
and the tracker comment all read ``Cost: $0.00`` for a job killed for
overspending.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.orchestration.budget import BudgetOverrunError
from embry0.services.issue_executor import IssueExecutor

_ISSUE = {"title": "t", "body": "", "repo": "foo/bar"}


def _executor(*, trace_cost: float = 0.0) -> IssueExecutor:
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock()
    ex._jobs.get = AsyncMock(return_value={"trace_id": None})
    ex._jobs.update = AsyncMock()
    ex._issues = MagicMock()
    ex._issues.update = AsyncMock()
    # #518: a job that ends reads its issue before putting it back to open.
    ex._issues.get = AsyncMock(return_value={"id": "iss-1", "status": "in_progress"})
    ex._traces = MagicMock()
    ex._traces.total_cost_for_job = AsyncMock(return_value=trace_cost)
    ex._sandbox = MagicMock()
    ex._sandbox.destroy = AsyncMock()
    ex._db = None
    ex._database_url = ""
    ex._audit_log_path = None
    ex._config = SimpleNamespace(environment_secret_key="k", pr_dedup_enabled=False)
    ex._env_repo = None
    ex._budget_gate = None
    ex._emit_audit = AsyncMock()
    ex._post_tracker_outcome = AsyncMock()
    return ex


def _raising_stream(node_outputs: dict[str, Any], exc: BaseException):
    """A stream that emits state (into the sink) and then dies mid-run."""

    async def _stream(input_value, issue_id, job_id, state_sink=None, **kw):
        if state_sink is not None:
            state_sink.update(node_outputs)
        raise exc

    return _stream


@pytest.mark.asyncio
async def test_hard_stop_destroys_the_sandbox_it_streamed():
    ex = _executor()
    ex._execute_workflow_stream = _raising_stream(  # type: ignore[assignment]
        {"sandbox_container_id": "ctr-abc", "total_cost_usd": 12.5},
        BudgetOverrunError(12.5, 10.0),
    )

    await ex._run_workflow("iss-1", "job-1", _ISSUE)

    ex._sandbox.destroy.assert_awaited_once_with("ctr-abc")


@pytest.mark.asyncio
async def test_hard_stop_persists_the_cost_from_traces():
    ex = _executor(trace_cost=12.5)
    ex._execute_workflow_stream = _raising_stream(  # type: ignore[assignment]
        {"sandbox_container_id": "ctr-abc", "total_cost_usd": 12.5},
        BudgetOverrunError(12.5, 10.0),
    )

    await ex._run_workflow("iss-1", "job-1", _ISSUE)

    kwargs = ex._jobs.update.await_args.kwargs
    assert kwargs["status"] == "failed"
    assert kwargs["error_code"] == "ERR_BUDGET_OVERRUN"
    assert kwargs["total_cost_usd"] == pytest.approx(12.5)


@pytest.mark.asyncio
async def test_a_crash_before_any_trace_falls_back_to_the_streamed_total():
    """resolve_job_total_cost prefers traces, but a job that died before
    writing one must not be zeroed out either."""
    ex = _executor(trace_cost=0.0)
    ex._execute_workflow_stream = _raising_stream(  # type: ignore[assignment]
        {"total_cost_usd": 3.75},
        RuntimeError("boom"),
    )

    await ex._run_workflow("iss-1", "job-1", _ISSUE)

    assert ex._jobs.update.await_args.kwargs["total_cost_usd"] == pytest.approx(3.75)


@pytest.mark.asyncio
async def test_a_crash_with_no_streamed_state_still_cleans_up_quietly():
    ex = _executor()
    ex._execute_workflow_stream = _raising_stream({}, RuntimeError("boom"))  # type: ignore[assignment]

    await ex._run_workflow("iss-1", "job-1", _ISSUE)

    ex._sandbox.destroy.assert_not_awaited()
    assert ex._jobs.update.await_args.kwargs["total_cost_usd"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_resume_hard_stop_destroys_the_sandbox_and_records_the_cost():
    """A resumed job owns a sandbox too, and its failure path destroyed
    nothing and recorded nothing."""
    ex = _executor(trace_cost=8.0)
    ex._execute_workflow_stream = _raising_stream(  # type: ignore[assignment]
        {"sandbox_container_id": "ctr-resume"},
        BudgetOverrunError(8.0, 5.0),
    )

    await ex.resume("iss-1", "job-1", answers=None)

    ex._sandbox.destroy.assert_awaited_once_with("ctr-resume")
    kwargs = ex._jobs.update.await_args.kwargs
    assert kwargs["error_code"] == "ERR_BUDGET_OVERRUN"
    assert kwargs["total_cost_usd"] == pytest.approx(8.0)


# --- jobs.budget_overrun_usd actually moves (#246) --------------------------


@pytest.mark.asyncio
async def test_hard_stop_persists_the_overrun_from_the_exception():
    """The node sets budget_overrun_usd in its `updates` and then raises, so
    those updates never reach state. The exception carries the figures."""
    ex = _executor(trace_cost=12.5)
    ex._execute_workflow_stream = _raising_stream(  # type: ignore[assignment]
        {"total_cost_usd": 12.5},
        BudgetOverrunError(12.5, 10.0),
    )

    await ex._run_workflow("iss-1", "job-1", _ISSUE)

    assert ex._jobs.update.await_args.kwargs["budget_overrun_usd"] == pytest.approx(2.5)


@pytest.mark.asyncio
async def test_a_non_budget_crash_persists_the_streamed_overrun():
    ex = _executor()
    ex._execute_workflow_stream = _raising_stream(  # type: ignore[assignment]
        {"budget_overrun_usd": 1.25},
        RuntimeError("boom"),
    )

    await ex._run_workflow("iss-1", "job-1", _ISSUE)

    assert ex._jobs.update.await_args.kwargs["budget_overrun_usd"] == pytest.approx(1.25)


@pytest.mark.asyncio
async def test_soft_overrun_is_persisted_on_the_completion_path():
    """`soft` mode records the overrun on state and lets the node finish, so
    the run ends normally — through _handle_workflow_result, which wrote
    total_cost_usd but never the overrun beside it."""
    ex = _executor(trace_cost=11.0)
    ex._webhook_notifier = None
    ex._github_token = ""
    ex._deliverables = None
    ex._persist_deliverables = AsyncMock(return_value=0)
    ex._issues.update_parent_status = AsyncMock()
    ex._issues.get = AsyncMock(return_value=None)

    await ex._handle_workflow_result(
        "iss-1",
        "job-1",
        {
            "current_stage": "complete",
            "final_status": "completed",
            "total_cost_usd": 11.0,
            "budget_overrun_usd": 1.0,
        },
    )

    assert ex._jobs.update.await_args.kwargs["budget_overrun_usd"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_resume_persists_the_overrun_too():
    ex = _executor(trace_cost=8.0)
    ex._execute_workflow_stream = _raising_stream(  # type: ignore[assignment]
        {},
        BudgetOverrunError(8.0, 5.0),
    )

    await ex.resume("iss-1", "job-1", answers=None)

    assert ex._jobs.update.await_args.kwargs["budget_overrun_usd"] == pytest.approx(3.0)
