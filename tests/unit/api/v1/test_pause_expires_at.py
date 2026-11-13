"""The paused banner counts down to the instant the expiry ends the pause (#471).

The banner counted 48 hours from ``finished_at || started_at``; the expiry
(``ContainerReaper._expire_paused_jobs``) ends a pause once ``updated_at`` is
older than ``paused_job_ttl_hours``, and every write refreshes ``updated_at``.
So after a resume that paused again the banner counted from the job's first
start, and could read "Expired" on a live job. ``GET /jobs/{job_id}`` now
serves ``pause_expires_at``, from the expiry's own rule, and these tests hold
the two together.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config


def _expires_at(job: dict[str, Any], ttl_hours: int) -> datetime | None:
    try:
        from embry0.execution.image_manager import paused_job_expires_at
    except ImportError:
        pytest.fail("there is no one rule for when a paused job's pause ends")
    return paused_job_expires_at(job, ttl_hours)


def test_a_pause_ends_its_ttl_after_the_jobs_last_write() -> None:
    last = datetime(2026, 11, 12, 9, 30, tzinfo=UTC)
    assert _expires_at({"status": "paused", "updated_at": last}, 48) == last + timedelta(hours=48)
    assert _expires_at({"status": "paused", "updated_at": last.replace(tzinfo=None)}, 10) == last + timedelta(hours=10)
    assert _expires_at({"status": "running", "updated_at": last}, 48) is None, "only a paused job expires"


@pytest.mark.asyncio
async def test_the_job_says_when_its_pause_ends() -> None:
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True, paused_job_ttl_hours=10))
    last = datetime.now(UTC) - timedelta(hours=1)
    jobs = MagicMock()
    jobs.get = AsyncMock(
        return_value={
            "job_id": "job-paused123",
            "status": "paused",
            "repo": "owner/repo",
            "task": "Fix it",
            # Started three days ago: a first pause, a resume and this second pause since.
            "started_at": last - timedelta(days=3),
            "finished_at": None,
            "updated_at": last,
        }
    )
    jobs.latest_interrupt = AsyncMock(return_value=None)
    app.state.jobs_repo = jobs
    app.state.traces_repo = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body = (await client.get("/api/v1/jobs/job-paused123", headers={"X-Requested-With": "XMLHttpRequest"})).json()
    assert body.get("pause_expires_at") == (last + timedelta(hours=10)).isoformat(), (
        f"the job does not say when its pause ends, by the expiry's TTL from its last write: {body.get('pause_expires_at')}"
    )


@pytest.mark.requires_postgres
@pytest.mark.asyncio
@pytest.mark.parametrize(("paused_for", "expired"), [(timedelta(hours=47), False), (timedelta(hours=49), True)])
async def test_the_expiry_ends_a_pause_when_the_job_says_it_will(
    db_with_migrations: Any, monkeypatch: pytest.MonkeyPatch, paused_for: timedelta, expired: bool
) -> None:
    """The instant the job serves is the one the sweeper keeps: before it the job stays paused, after it expires."""
    import embry0.orchestration.checkpoint as checkpoint
    from embry0.storage.repositories.jobs import JobsRepository
    from tests.unit.execution.test_image_manager import _reaper

    monkeypatch.setattr(checkpoint, "purge_thread", AsyncMock())
    db = db_with_migrations
    jobs = JobsRepository(db)
    job_id = await jobs.create(repo="acme/app", task="Fix the idle timer")
    await jobs.update(job_id, status="running")
    await jobs.update(job_id, status="paused")
    async with db.transaction() as conn:
        # The trigger would set updated_at to now: the pause began paused_for ago.
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute("UPDATE jobs SET updated_at = NOW() - $2::interval WHERE job_id = $1", job_id, paused_for)
    ends = _expires_at(await jobs.get(job_id) or {}, 48)
    assert ends is not None
    assert (ends < datetime.now(UTC)) is expired, f"the pause ends at {ends} after {paused_for}"
    docker = MagicMock(run_cmd=AsyncMock(return_value=""))
    reaper = _reaper(docker, db=db, jobs_repo=jobs, paused_ttl_hours=48)
    await reaper._expire_paused_jobs()
    status = (await jobs.get(job_id) or {}).get("status")
    assert status == ("expired" if expired else "paused"), (
        f"the expiry and the job's pause_expires_at disagree after {paused_for}: {status}, ends {ends}"
    )
