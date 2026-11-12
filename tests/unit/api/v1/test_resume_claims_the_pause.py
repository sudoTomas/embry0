"""Two resumes of one paused job at once: only one runs it (#470).

``POST /jobs/{job_id}/resume`` read the job, checked it was ``paused``, and
scheduled the resume; the resume wrote ``running`` later, and a second write
of the same status is a no-op. So a second request that passed the same check
before the first resume ran (a double click, an API caller's retry) scheduled
a second resume of the same checkpoint: both asked the forge for a change
request, the second overwrote the first's ``pr_url`` with None, and the
sandbox was destroyed twice. Now the route takes the pause, ``paused`` to
``running`` in one statement, before it schedules anything, and the loser is
answered 409 with what the job is now.

The race is made deterministic: both requests read the job as ``paused``
before either goes on.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.storage.repositories.jobs import JobsRepository

pytestmark = pytest.mark.requires_postgres

HEADERS = {"X-Requested-With": "XMLHttpRequest"}


class _BothReadPaused(JobsRepository):
    """The jobs repository, with each request's first read of the job held until both requests have made it."""

    def __init__(self, db: Any, readers: int) -> None:
        super().__init__(db)
        self.barrier = asyncio.Barrier(readers)
        self.held = 0

    async def get(self, job_id: str) -> Any:
        row = await super().get(job_id)
        if self.held < self.barrier.parties:
            self.held += 1
            await asyncio.wait_for(self.barrier.wait(), timeout=5)
        return row


async def _paused_job(jobs: JobsRepository) -> str:
    job_id = await jobs.create(repo="acme/app", task="Fix the idle timer")
    await jobs.update(job_id, status="running")
    await jobs.update(job_id, status="paused")
    return job_id


def _app(jobs: JobsRepository) -> Any:
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    app.state.jobs_repo = jobs
    app.state.issue_executor = MagicMock(schedule_resume=MagicMock(), cancel_job=AsyncMock(), draining=False)
    return app


@pytest.mark.asyncio
async def test_two_resumes_of_one_pause_at_once_run_it_once(db_with_migrations: Any) -> None:
    jobs = _BothReadPaused(db_with_migrations, readers=2)
    job_id = await _paused_job(JobsRepository(db_with_migrations))
    app = _app(jobs)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first, second = await asyncio.gather(
            *(
                client.post(f"/api/v1/jobs/{job_id}/resume", json={"choice": "merge_as_is"}, headers=HEADERS)
                for _ in range(2)
            )
        )
    scheduled = app.state.issue_executor.schedule_resume.call_count
    assert scheduled == 1, f"two resumes of one pause were both scheduled ({scheduled}): {first.text} / {second.text}"
    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [200, 409], f"one resume runs and the other is refused: {statuses}"
    refused = first if first.status_code == 409 else second
    assert refused.json()["detail"] == "Job is running, not paused", refused.json()
    assert (await JobsRepository(db_with_migrations).get(job_id))["status"] == "running"
    assert app.state.issue_executor.schedule_resume.call_args.kwargs.get("claimed") is True, (
        "the resume is not told the route already took the pause"
    )


@pytest.mark.asyncio
async def test_a_resume_refused_for_a_shutdown_gives_the_pause_back(db_with_migrations: Any) -> None:
    from embry0.orchestration.restart import ExecutorDraining

    jobs = JobsRepository(db_with_migrations)
    job_id = await _paused_job(jobs)
    app = _app(jobs)
    app.state.issue_executor.schedule_resume = MagicMock(side_effect=ExecutorDraining("shutting down"))
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/v1/jobs/{job_id}/resume", json={"choice": "abandon"}, headers=HEADERS)
    assert resp.status_code == 503, f"a resume a shutdown refused must say so: {resp.status_code} {resp.text}"
    assert (await jobs.get(job_id))["status"] == "paused", "a resume that never ran must leave the job paused"


@pytest.mark.asyncio
async def test_the_expiry_and_a_resume_cannot_both_take_one_pause(db_with_migrations: Any) -> None:
    """The paused-job expiry claims paused -> expired; once a resume has taken the pause, it cannot."""
    from embry0.storage.repositories.jobs import StatusTransitionConflict

    jobs = JobsRepository(db_with_migrations)
    job_id = await _paused_job(jobs)
    if not hasattr(jobs, "claim_pause"):
        pytest.fail("the jobs repository has no way to take a pause for one resume")
    assert await jobs.claim_pause(job_id) is True
    assert await jobs.claim_pause(job_id) is False, "one pause was taken twice"
    with pytest.raises((ValueError, StatusTransitionConflict)):
        await jobs.update(job_id, status="expired")
    assert (await jobs.get(job_id))["status"] == "running"
