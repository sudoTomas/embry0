"""The job.completed / job.failed webhook payload must identify the job.

RAV-1074 gave consumers a clean summary but nothing to identify the job BY:
every notification opened with a job id, so they all read alike and a reader
had to open the dashboard to learn what the job even was.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from embry0.services.issue_executor import IssueExecutor


def _executor(job_row=None):
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock()
    ex._jobs.update = AsyncMock()
    ex._jobs.get = AsyncMock(return_value=job_row)
    ex._issues = MagicMock()
    ex._issues.update = AsyncMock()
    ex._issues.update_parent_status = AsyncMock()
    ex._db = None
    ex._audit_log_path = None
    ex._database_url = "postgresql://unused"
    ex._webhook_notifier = MagicMock()
    ex._webhook_notifier.emit_soon = MagicMock()
    return ex


async def _run(ex, result):
    with (
        patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()),
        patch("embry0.orchestration.checkpoint.purge_thread", new=AsyncMock()),
    ):
        await ex._handle_workflow_result("iss-1", "job-1", result)
    event, data = ex._webhook_notifier.emit_soon.call_args_list[0].args
    return event, data


@pytest.mark.asyncio
async def test_payload_carries_the_title_from_the_job_row():
    """`result` is a merge of node updates and rarely holds `task` (RAV-1282)."""
    ex = _executor(
        job_row={
            "repo": "acme/app",
            "job_kind": "code",
            "task": "feat(tools): add PDF compression\n\n## Why\n\nbody...",
        }
    )
    event, data = await _run(ex, {"agent_outputs": [{"agent_type": "review", "is_error": False}]})

    assert event == "job.completed"
    assert data["title"] == "feat(tools): add PDF compression"
    # falls back to the row for these too, rather than emitting nulls
    assert data["repo"] == "acme/app"
    assert data["kind"] == "code"


@pytest.mark.asyncio
async def test_result_task_wins_over_the_job_row():
    ex = _executor(job_row={"task": "stale row title"})
    _event, data = await _run(
        ex,
        {"task": "fresher title from state\n\nbody", "agent_outputs": [{"agent_type": "review", "is_error": False}]},
    )
    assert data["title"] == "fresher title from state"


@pytest.mark.asyncio
async def test_title_key_is_always_present_even_with_nothing_to_put_in_it():
    """Consumers can read data["title"] unconditionally."""
    ex = _executor(job_row=None)
    _event, data = await _run(ex, {"agent_outputs": [{"agent_type": "review", "is_error": False}]})
    assert data["title"] == ""


@pytest.mark.asyncio
async def test_a_failed_job_is_identified_too():
    ex = _executor(job_row={"task": "fix(api): stop double-charging\n\nbody", "repo": "acme/app"})
    event, data = await _run(
        ex,
        {"current_stage": "failed", "agent_outputs": [{"agent_type": "developer", "is_error": True}]},
    )
    assert event == "job.failed"
    assert data["title"] == "fix(api): stop double-charging"


@pytest.mark.asyncio
async def test_a_job_row_lookup_failure_does_not_break_the_emit():
    ex = _executor()
    ex._jobs.get = AsyncMock(side_effect=RuntimeError("db down"))
    _event, data = await _run(ex, {"agent_outputs": [{"agent_type": "review", "is_error": False}]})
    assert data["title"] == ""


@pytest.mark.asyncio
async def test_duplicate_pr_skip_payload_carries_the_title_too():
    """_skip_as_duplicate emits job.skipped (#474) — same title contract."""
    ex = _executor()
    ex._emit_audit = AsyncMock()
    await ex._skip_as_duplicate(
        "iss-1",
        "job-1",
        "acme/app",
        {"number": 7, "url": "https://github.com/acme/app/pull/7", "matched_by": "ticket"},
        task="feat(tools): add PDF compression\n\nbody...",
    )
    _event, data = ex._webhook_notifier.emit_soon.call_args_list[0].args
    assert _event == "job.skipped"
    assert data["title"] == "feat(tools): add PDF compression"
    assert data["pr_url"] is None
    assert data["existing_pr_url"] == "https://github.com/acme/app/pull/7"


@pytest.mark.asyncio
async def test_duplicate_pr_skip_title_is_present_even_without_a_task():
    ex = _executor()
    ex._emit_audit = AsyncMock()
    await ex._skip_as_duplicate(
        "iss-1",
        "job-1",
        "acme/app",
        {"number": 7, "url": "https://github.com/acme/app/pull/7", "matched_by": "ticket"},
    )
    _event, data = ex._webhook_notifier.emit_soon.call_args_list[0].args
    assert data["title"] == ""


@pytest.mark.asyncio
async def test_the_brief_title_wins_once_the_job_has_one():
    """A job's brief says in plain words what it is (spec 2026-10-26, job briefs)."""
    ex = _executor(job_row={"task": "[KEY-1] [watcher] Diagnose boot crash-loop that 502s both copies"})
    with patch(
        "embry0.services._executor_outcomes._brief_title",
        new=AsyncMock(return_value="Find why the service crash-looped"),
    ):
        _, data = await _run(ex, {"agent_outputs": [{"agent_type": "review", "is_error": False}]})
    assert data["title"] == "Find why the service crash-looped"


@pytest.mark.asyncio
async def test_an_unreadable_brief_falls_back_to_the_task_line():
    ex = _executor(job_row={"task": "fix(api): stop double-charging"})
    ex._db = object()
    with patch(
        "embry0.storage.repositories.job_briefs.JobBriefsRepository.current",
        new=AsyncMock(side_effect=RuntimeError("down")),
    ):
        _, data = await _run(ex, {"agent_outputs": [{"agent_type": "review", "is_error": False}]})
    assert data["title"] == "fix(api): stop double-charging"
