"""Agent session retention (ADR 0017): its own setting; old sessions of ended jobs go, the rest stay."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.config import Embry0Config
from embry0.services.agent_session_retention import AgentSessionRetentionSweep
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.agent_sessions import AgentSessionsRepository
from embry0.storage.repositories.jobs import JobsRepository

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=40)


def test_the_setting_is_unset_by_default() -> None:
    assert Embry0Config(_env_file=None).agent_session_retention_days is None
    assert Embry0Config(_env_file=None, agent_session_retention_days="").agent_session_retention_days is None


@pytest.mark.asyncio
async def test_unset_means_nothing_is_read_or_deleted() -> None:
    repo = MagicMock()
    repo.expired = AsyncMock()
    repo.delete_expired = AsyncMock()
    sweep = AgentSessionRetentionSweep(repo, retention_days=None)

    result = await sweep.run_once()

    assert not sweep.enabled
    assert (result.sessions, result.bytes) == (0, 0)
    repo.expired.assert_not_called()
    repo.delete_expired.assert_not_called()


async def _job(db: DatabasePool, status: str) -> str:
    job_id = await JobsRepository(db).create(repo="acme/widgets", task="t")
    await db.execute("UPDATE jobs SET status = $2 WHERE job_id = $1", job_id, status)
    return job_id


async def _session(db: DatabasePool, job_id: str, last_turn_at: datetime, blob: bytes = b"transcript") -> None:
    await AgentSessionsRepository(db).upsert(
        job_id=job_id, agent_type="developer", mode="claude_max", session_id="sess", session_blob=blob
    )
    await db.execute("UPDATE agent_sessions SET last_turn_at = $2 WHERE job_id = $1", job_id, last_turn_at)


async def _sessions(db: DatabasePool) -> set[str]:
    return {r["job_id"] for r in await db.fetch("SELECT job_id FROM agent_sessions")}


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_old_sessions_of_ended_jobs_are_deleted_and_the_rest_kept(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    old_done = await _job(db, "completed")
    old_failed = await _job(db, "failed")
    new_done = await _job(db, "completed")
    old_live = await _job(db, "awaiting_input")
    await _session(db, old_done, OLD, blob=b"x" * 1000)
    await _session(db, old_failed, OLD)
    await _session(db, new_done, NOW)
    await _session(db, old_live, OLD)
    sweep = AgentSessionRetentionSweep(AgentSessionsRepository(db), retention_days=30, batch=1)

    preview = await sweep.run_once(dry_run=True, now=NOW)
    assert await _sessions(db) == {old_done, old_failed, new_done, old_live}, "a dry run deleted a session"
    result = await sweep.run_once(now=NOW)

    assert await _sessions(db) == {new_done, old_live}, (
        "only ended jobs' sessions untouched for longer than the retention may go; a recent one or a live job's stays"
    )
    assert result.sessions == preview.sessions == 2, "batches of one must still reach every expired session"
    assert result.bytes == preview.bytes >= 1000
    again = await sweep.run_once(now=NOW)
    assert (again.sessions, again.bytes) == (0, 0), "a second sweep found more to delete"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_unset_keeps_every_session(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    old_done = await _job(db, "completed")
    await _session(db, old_done, OLD)

    await AgentSessionRetentionSweep(AgentSessionsRepository(db), retention_days=None).run_once(now=NOW)

    assert await _sessions(db) == {old_done}
