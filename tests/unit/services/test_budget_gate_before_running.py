"""The budget gate is asked before a job is marked running (#348).

A job the dispatch gate refuses never ran an agent turn. Before #348 every
runner marked the job ``running`` with a ``started_at`` first and asked the
gate second, so a refusal looked like a job that started and ran. These tests
run each of the four runners against a real ``jobs`` row.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from embry0.orchestration.budget_gate import DispatchDecision
from embry0.safety.error_codes import ErrorCode
from embry0.services.issue_executor import IssueExecutor
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.jobs import JobsRepository
from embry0.storage.schemas import JobStatus

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _executor(jobs: JobsRepository, *, allowed: bool) -> IssueExecutor:
    executor = IssueExecutor(
        issues_repo=MagicMock(),
        jobs_repo=jobs,
        traces_repo=MagicMock(),
        workflow_registry=MagicMock(),
        database_url="postgresql://unused/unused",
    )
    executor._issues.update = AsyncMock()
    executor._audit_log_path = None
    gate = MagicMock()
    gate.check = AsyncMock(return_value=DispatchDecision(allowed=allowed, limit="daily", message="capped"))
    executor._budget_gate = gate
    executor._post_tracker_outcome = AsyncMock()  # type: ignore[method-assign]
    executor._finish_credential_uses = AsyncMock()  # type: ignore[method-assign]
    executor._load_merged_env_vars = AsyncMock(return_value=None)  # type: ignore[method-assign]
    return executor


async def _run(executor: IssueExecutor, runner: str, job_id: str) -> None:
    if runner == "issue_to_pr":
        await executor._run_workflow(None, job_id, {"title": "t", "body": ""})
    elif runner == "qa":
        await executor._run_qa_workflow(job_id=job_id, repo="acme/widgets", branch="feature/x", qa_overrides={})
    elif runner == "pr_review":
        await executor._run_pr_review_workflow(job_id)
    else:
        await executor._run_onboard_workflow(job_id, repo="acme/widgets", branch="main", skip_smoke=True)


RUNNERS = ["issue_to_pr", "qa", "onboard", "pr_review"]


@pytest.mark.parametrize("runner", RUNNERS)
async def test_a_refused_job_never_reaches_running_and_has_no_started_at(
    db_with_migrations: DatabasePool, runner: str
) -> None:
    jobs = JobsRepository(db_with_migrations)
    job_id = await jobs.create(repo="acme/widgets", task="t")
    executor = _executor(jobs, allowed=False)
    stream = AsyncMock(return_value=(None, False, None))

    with (
        patch.object(executor, "_execute_workflow_stream", stream),
        patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()),
    ):
        await _run(executor, runner, job_id)

    row = await jobs.get(job_id)
    assert row is not None
    assert row["status"] == JobStatus.FAILED, f"{runner}: a refused job ended {row['status']!r}"
    assert row["error_code"] == ErrorCode.BUDGET_REFUSED.value, f"{runner}: refusal code {row['error_code']!r}"
    assert row["started_at"] is None, f"{runner}: a gate-refused job carries started_at={row['started_at']}"
    assert row["finished_at"] is not None, f"{runner}: a refused job has no finished_at"
    stream.assert_not_awaited()


@pytest.mark.parametrize("runner", RUNNERS)
async def test_an_allowed_job_is_marked_running_after_the_gate(db_with_migrations: DatabasePool, runner: str) -> None:
    """The reorder must not lose the running mark for a job the gate lets through."""
    jobs = JobsRepository(db_with_migrations)
    job_id = await jobs.create(repo="acme/widgets", task="t")
    executor = _executor(jobs, allowed=True)
    seen: dict[str, object] = {}

    async def _stream(*_args: object, **_kwargs: object) -> tuple[None, bool, None]:
        row = await jobs.get(job_id)
        assert row is not None
        seen.update(status=row["status"], started_at=row["started_at"])
        return None, False, None

    with (
        patch.object(executor, "_execute_workflow_stream", _stream),
        patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()),
    ):
        await _run(executor, runner, job_id)

    assert seen.get("status") == JobStatus.RUNNING, f"{runner}: the workflow started on a {seen.get('status')!r} job"
    assert seen.get("started_at") is not None, f"{runner}: a running job has no started_at"
