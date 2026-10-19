"""A job with no issue writes no issue status audit row.

Every log-watcher analysis job is issue-less, and each one wrote an
``issue.status_changed`` row with a null issue and a made-up transition
(triaging to closed): about seven audit rows in ten were that.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from embry0.services.issue_executor import IssueExecutor

_COMPLETED = {"agent_outputs": [{"agent_type": "analysis", "is_error": False}]}


def _executor() -> IssueExecutor:
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock()
    ex._jobs.update = AsyncMock()
    ex._jobs.get = AsyncMock(return_value={"trace_id": None})
    ex._issues = MagicMock()
    ex._issues.update = AsyncMock()
    ex._issues.update_parent_status = AsyncMock()
    ex._traces = MagicMock()
    ex._traces.total_cost_for_job = AsyncMock(return_value=0.0)
    ex._sandbox = MagicMock()
    ex._sandbox.destroy = AsyncMock()
    ex._db = None
    ex._audit_log_path = None
    ex._database_url = ""
    ex._config = SimpleNamespace(environment_secret_key="k", pr_dedup_enabled=False)
    ex._env_repo = None
    ex._budget_gate = None
    ex._webhook_notifier = None
    ex._emit_audit = AsyncMock()
    ex._post_tracker_outcome = AsyncMock()
    return ex


def _status_rows(ex: IssueExecutor) -> list[Any]:
    return [c for c in ex._emit_audit.await_args_list if c.args[1] == "issue.status_changed"]


async def _complete(ex: IssueExecutor, issue_id: str | None) -> None:
    with patch("embry0.orchestration.checkpoint.purge_thread", new=AsyncMock()):
        await ex._handle_workflow_result(issue_id, "job-1", dict(_COMPLETED))


async def _crash(ex: IssueExecutor, issue_id: str | None) -> None:
    ex._execute_workflow_stream = _crashing_stream  # type: ignore[assignment]
    with patch("embry0.orchestration.checkpoint.purge_thread", new=AsyncMock()):
        await ex._run_workflow(issue_id, "job-1", {"title": "t", "body": "", "repo": "acme/app"})


@pytest.mark.asyncio
async def test_a_completed_job_with_no_issue_writes_no_status_row():
    ex = _executor()
    await _complete(ex, None)
    rows = _status_rows(ex)
    assert rows == [], f"an issue-less job wrote {len(rows)} issue.status_changed row(s): {rows}"


@pytest.mark.asyncio
async def test_a_completed_job_with_an_issue_still_writes_its_status_row():
    ex = _executor()
    await _complete(ex, "iss-1")
    [row] = _status_rows(ex)
    assert row.kwargs["issue_id"] == "iss-1"
    assert row.kwargs["details"]["new_status"] == "closed"


async def _crashing_stream(input_value, issue_id, job_id, state_sink=None, **kw):
    """A workflow that dies mid-run, as in test_budget_hard_stop."""
    raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_a_crashed_job_with_no_issue_writes_no_status_row():
    ex = _executor()
    await _crash(ex, None)
    assert ex._jobs.update.await_args.kwargs["status"] == "failed"
    rows = _status_rows(ex)
    assert rows == [], f"an issue-less crashed job wrote {len(rows)} issue.status_changed row(s): {rows}"


@pytest.mark.asyncio
async def test_a_crashed_job_with_an_issue_still_writes_its_status_row():
    ex = _executor()
    await _crash(ex, "iss-1")
    [row] = _status_rows(ex)
    assert row.kwargs["issue_id"] == "iss-1"
    assert row.kwargs["details"]["new_status"] == "open"
