"""A completed standards audit's score lands on its repository's state (ADR 0014)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from embry0.services import _executor_outcomes
from embry0.services.issue_executor import IssueExecutor
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.jobs import JobsRepository
from embry0.storage.repositories.standards import RepoStandardStateRepository

REPORT = (Path(__file__).resolve().parents[2] / "fixtures" / "standards-audit" / "scored-report.md").read_text()
CLEAN_RUN: dict[str, Any] = {
    "agent_outputs": [{"agent_type": "analysis", "is_error": False}],
    "deliverables": [{"type": "report", "title": "analysis report", "content": REPORT}],
}


def _executor() -> IssueExecutor:
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock()
    ex._jobs.update = AsyncMock()
    ex._jobs.get = AsyncMock(return_value={"repo": "acme/widgets", "task": "Design audit"})
    ex._issues = MagicMock()
    ex._issues.update = AsyncMock()
    ex._issues.update_parent_status = AsyncMock()
    ex._db = None
    ex._audit_log_path = None
    ex._database_url = "postgresql://unused"
    ex._webhook_notifier = None
    ex._merge_approvals = None
    return ex


async def _run(ex: IssueExecutor, result: dict[str, Any], recorder: AsyncMock) -> None:
    with (
        patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()),
        patch("embry0.orchestration.checkpoint.purge_thread", new=AsyncMock()),
        patch("embry0.services._executor_outcomes.record_case_event", new=AsyncMock()),
        patch("embry0.services._executor_outcomes._publish_verification", new=AsyncMock()),
        patch("embry0.services._executor_outcomes._record_standard_audit", new=recorder),
    ):
        await ex._handle_workflow_result(None, "job-1", result)


async def test_a_completed_job_is_offered_to_the_standards_recorder() -> None:
    ex = _executor()
    recorder = AsyncMock()
    await _run(ex, CLEAN_RUN, recorder)
    assert ex._jobs.update.await_args.kwargs["status"] == "completed"
    recorder.assert_awaited_once_with(ex, "job-1", CLEAN_RUN)


async def test_a_failed_job_is_not() -> None:
    ex = _executor()
    recorder = AsyncMock()
    await _run(ex, {"current_stage": "failed", "error_code": "ERR_X"}, recorder)
    assert ex._jobs.update.await_args.kwargs["status"] == "failed", "the fixture did not produce a failed job"
    recorder.assert_not_awaited()


async def test_a_failing_recorder_never_touches_the_job_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    ex = _executor()
    ex._db = MagicMock()
    ex._persist_deliverables = AsyncMock(return_value=1)  # type: ignore[method-assign]
    broken = MagicMock()
    broken.record_audit = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr("embry0.storage.repositories.standards.RepoStandardStateRepository", lambda _db: broken)
    purge = AsyncMock()
    with (
        patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()),
        patch("embry0.orchestration.checkpoint.purge_thread", new=purge),
        patch("embry0.services._executor_outcomes.record_case_event", new=AsyncMock()),
        patch("embry0.services._executor_outcomes._publish_verification", new=AsyncMock()),
    ):
        await ex._handle_workflow_result(None, "job-1", CLEAN_RUN)
    broken.record_audit.assert_awaited_once()
    assert ex._jobs.update.await_args.kwargs["status"] == "completed"
    # The handler ran to its end: the checkpoint purge is the last step of an
    # issue-less job, which has no issue status to update.
    purge.assert_awaited_once()


async def test_no_database_is_a_no_op() -> None:
    ex = _executor()
    await _executor_outcomes._record_standard_audit(ex, "job-1", CLEAN_RUN)


@pytest.mark.requires_postgres
async def test_the_newest_audit_writes_its_score_and_findings(db_with_migrations: DatabasePool) -> None:
    jobs = JobsRepository(db_with_migrations)
    states = RepoStandardStateRepository(db_with_migrations)
    audit = await jobs.create(repo="acme/widgets", task="Design audit", pipeline_template="design-audit")
    other = await jobs.create(repo="acme/widgets", task="Fix a bug")
    await states.set_mode("acme/widgets", "design", "advise")
    await states.start_audit("acme/widgets", "design", audit, expected_job_id=None)

    ex = _executor()
    ex._db = db_with_migrations
    await _executor_outcomes._record_standard_audit(ex, other, CLEAN_RUN)
    untouched = await states.get("acme/widgets", "design")
    assert untouched is not None and (untouched.score, untouched.last_audit_at) == (None, None)

    await _executor_outcomes._record_standard_audit(ex, audit, CLEAN_RUN)
    state = await states.get("acme/widgets", "design")
    assert state is not None
    assert (state.score, state.findings_open, state.last_audit_job_id) == (72, 3, audit)
    assert state.last_audit_at is not None
