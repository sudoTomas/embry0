"""A run's error handler leaves a job that already ended as it ended (#594, #595).

A job a person cancelled while its run went on, or one the budget gate had
failed already, is not the run's to fail again. ``cancelled -> failed`` is
refused, so the handler raised: the background task logged
``background_task_failed`` and broadcast ``job_failed`` for a cancelled job,
and skipped its own cleanup. ``failed -> failed`` is allowed as a same-status
write, so a refusal whose later step raised was written over: its code
became ``ERR_STORAGE`` and its end was announced twice. These tests run the
runners against a real ``jobs`` row.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from embry0.services import _executor_run
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.issues import IssuesRepository
from embry0.storage.repositories.jobs import JobsRepository
from tests.unit.services.test_budget_gate_before_running import RUNNERS, _executor, _run

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

LOST = "connection was closed in the middle of operation"


def _counting(ex: Any) -> dict[str, list[Any]]:
    seen: dict[str, list[Any]] = {"announced": [], "stamped": [], "cleaned": [], "fallback": []}
    ex._announce_finished = AsyncMock(side_effect=lambda job_id: seen["announced"].append(job_id))
    ex._finish_credential_uses = AsyncMock(side_effect=lambda job_id, error_code: seen["stamped"].append(error_code))
    ex._cleanup_sandbox = AsyncMock(side_effect=lambda state, job_id: seen["cleaned"].append(job_id))
    ex._open_fallback_pr = AsyncMock(side_effect=lambda *a, **k: (seen["fallback"].append(a[1]), (None, None))[1])
    ex._traces.total_cost_for_job = AsyncMock(return_value=0.0)
    return seen


# ---- #594: the refusal stands -------------------------------------------------------------------------


@pytest.mark.parametrize("fails", ["once", "always"])
async def test_a_refusal_stands_when_putting_its_issue_back_fails(db_with_migrations: DatabasePool, fails: str) -> None:
    issues = IssuesRepository(db_with_migrations)
    issue_id = await issues.create("Fix the idle timer", repo="acme/widgets")
    await issues.update(issue_id, status="triaging")
    jobs = JobsRepository(db_with_migrations)
    job_id = await jobs.create(repo="acme/widgets", task="t", issue_id=issue_id)
    ex = _executor(jobs, allowed=False)
    ex._issues = issues
    seen = _counting(ex)
    write = issues.update
    tries: list[int] = []

    async def update(iid: str, **fields: Any) -> Any:
        if fields.get("status") == "open":
            tries.append(1)
            if fails == "always" or len(tries) == 1:
                raise asyncpg.exceptions.ConnectionDoesNotExistError(LOST)
        return await write(iid, **fields)

    issues.update = update  # type: ignore[method-assign]
    stream = AsyncMock(return_value=(None, False, None))
    with (
        patch.object(ex, "_execute_workflow_stream", stream),
        patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()),
    ):
        await ex._run_workflow(issue_id, job_id, {"title": "t", "body": "", "repo": "acme/widgets"})

    row = await jobs.get(job_id) or {}
    assert (row.get("status"), row.get("error_code")) == ("failed", "ERR_BUDGET_REFUSED"), (
        f"the refusal is written over: {row.get('error_code')} {row.get('error_message')!r}"
    )
    assert LOST not in str(row.get("error_message")), row.get("error_message")
    assert seen["announced"] == [job_id], f"announced {len(seen['announced'])} times"
    assert seen["stamped"] == ["ERR_BUDGET_REFUSED"], seen["stamped"]
    stream.assert_not_awaited()
    expected = "open" if fails == "once" else "triaging"
    assert (await issues.get(issue_id) or {}).get("status") == expected, "the issue is not put back when it can be"


# ---- #595: a job cancelled meanwhile stays cancelled ------------------------------------------------------


@pytest.mark.parametrize("runner", RUNNERS)
async def test_a_job_cancelled_during_its_run_stays_cancelled(db_with_migrations: DatabasePool, runner: str) -> None:
    jobs = JobsRepository(db_with_migrations)
    job_id = await jobs.create(repo="acme/widgets", task="t")
    ex = _executor(jobs, allowed=True)
    seen = _counting(ex)

    async def stream(*_a: Any, **_k: Any) -> Any:
        await jobs.update(job_id, status="cancelled", error_message="cancelled by a person")
        raise RuntimeError("the sandbox went away")

    with (
        patch.object(ex, "_execute_workflow_stream", stream),
        patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()),
    ):
        await _run(ex, runner, job_id)  # raised ValueError: cancelled -> failed

    row = await jobs.get(job_id) or {}
    assert (row.get("status"), row.get("error_code"), row.get("error_message")) == (
        "cancelled",
        None,
        "cancelled by a person",
    ), f"{runner}: {row.get('status')} {row.get('error_code')} {row.get('error_message')!r}"
    assert seen["announced"] == [] and seen["stamped"] == [], f"{runner}: the handler ended the job again: {seen}"
    assert seen["fallback"] == [], f"{runner}: a pull request is opened for a cancelled job"
    if runner in ("issue_to_pr", "qa", "pr_review"):
        assert seen["cleaned"] == [job_id], f"{runner}: the run does not clean up after itself"


async def test_a_job_cancelled_during_its_resumed_run_stays_cancelled(db_with_migrations: DatabasePool) -> None:
    issues = IssuesRepository(db_with_migrations)
    issue_id = await issues.create("Fix the idle timer", repo="acme/widgets")
    jobs = JobsRepository(db_with_migrations)
    job_id = await jobs.create(repo="acme/widgets", task="t", issue_id=issue_id)
    await jobs.update(job_id, status="running")
    await jobs.update(job_id, status="cancelled", error_message="cancelled by a person")
    ex = _executor(jobs, allowed=True)
    ex._issues = issues
    seen = _counting(ex)

    await ex._fail_resume(issue_id, job_id, RuntimeError("the sandbox went away"), None, {})

    row = await jobs.get(job_id) or {}
    assert (row.get("status"), row.get("error_code")) == ("cancelled", None), dict(row)
    assert seen == {"announced": [], "stamped": [], "cleaned": [job_id], "fallback": []}, seen


# ---- the guard itself -------------------------------------------------------------------------------------


async def test_a_failure_is_not_written_over_a_job_that_already_failed(db_with_migrations: DatabasePool) -> None:
    jobs = JobsRepository(db_with_migrations)
    job_id = await jobs.create(repo="acme/widgets", task="t")
    await jobs.update(
        job_id, status="failed", error_code="ERR_BUDGET_REFUSED", error_message="a cap", before_start=True
    )
    ex = _executor(jobs, allowed=True)
    seen = _counting(ex)

    written = await _executor_run._fail_the_run(ex, job_id, error_message=LOST, error_code="ERR_STORAGE")

    row = await jobs.get(job_id) or {}
    assert written is False and (row.get("error_code"), row.get("error_message")) == ("ERR_BUDGET_REFUSED", "a cap")
    assert seen["announced"] == [] and seen["stamped"] == []


async def test_a_job_that_ends_between_the_check_and_the_write_is_left_as_it_ended(
    db_with_migrations: DatabasePool,
) -> None:
    jobs = JobsRepository(db_with_migrations)
    job_id = await jobs.create(repo="acme/widgets", task="t")
    await jobs.update(job_id, status="running")
    ex = _executor(jobs, allowed=True)
    seen = _counting(ex)
    read = jobs.status
    reads: list[str | None] = []

    async def status(jid: str) -> str | None:
        current = await read(jid)
        reads.append(current)
        if len(reads) == 1:  # a person cancels it right after the check
            await jobs.update(jid, status="cancelled")
        return current

    jobs.status = status  # type: ignore[method-assign]
    written = await _executor_run._fail_the_run(ex, job_id, error_message="boom", error_code="ERR_UNKNOWN")

    assert written is False and reads == ["running", "cancelled"], reads
    assert (await jobs.get(job_id) or {}).get("status") == "cancelled"
    assert seen["announced"] == []


async def test_a_status_it_cannot_read_leaves_the_write_to_decide(db_with_migrations: DatabasePool) -> None:
    jobs = JobsRepository(db_with_migrations)
    job_id = await jobs.create(repo="acme/widgets", task="t")
    await jobs.update(job_id, status="running")
    ex = _executor(jobs, allowed=True)
    seen = _counting(ex)
    jobs.status = AsyncMock(side_effect=asyncpg.exceptions.ConnectionDoesNotExistError(LOST))  # type: ignore[method-assign]

    written = await _executor_run._fail_the_run(ex, job_id, error_message="boom", error_code="ERR_UNKNOWN")

    assert written is True and (await jobs.get(job_id) or {}).get("status") == "failed"
    assert seen["announced"] == [job_id] and seen["stamped"] == ["ERR_UNKNOWN"]


async def test_the_issue_of_a_job_cancelled_during_its_run_is_left_to_the_cancel(
    db_with_migrations: DatabasePool,
) -> None:
    """What ended the job deals with its issue: the run records no "Workflow failed" status change for it."""
    issues = IssuesRepository(db_with_migrations)
    issue_id = await issues.create("Fix the idle timer", repo="acme/widgets")
    await issues.update(issue_id, status="triaging")
    jobs = JobsRepository(db_with_migrations)
    job_id = await jobs.create(repo="acme/widgets", task="t", issue_id=issue_id)
    ex = _executor(jobs, allowed=True)
    ex._issues = issues
    _counting(ex)

    async def stream(*_a: Any, **_k: Any) -> Any:
        await jobs.update(job_id, status="cancelled", error_message="cancelled by a person")
        raise RuntimeError("the sandbox went away")

    audit = AsyncMock()
    with (
        patch.object(ex, "_execute_workflow_stream", stream),
        patch("embry0.services.issue_executor.emit_audit", audit),
    ):
        await ex._run_workflow(issue_id, job_id, {"title": "t", "body": "", "repo": "acme/widgets"})

    actions = [c.args[1] for c in audit.await_args_list]
    assert "issue.status_changed" not in actions, f"the run recorded its failure on the issue: {actions}"
    assert (await issues.get(issue_id) or {}).get("status") == "triaging", "the run moved the cancelled job's issue"
