"""Migration 116 — spent account waits: what a parked job waits for, and until when."""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_116_present() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(116) == 1, "migration 116 (spent account waits) must exist exactly once"
    sql = next(s for v, _, s in MIGRATIONS if v == 116)
    assert "account_wait" in sql and "JSONB" in sql


def test_migration_116_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_115.py)."""
    assert MIGRATIONS[-1][0] == 116, "migration 116 must add jobs.account_wait"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_parked_job_is_listed_and_claimed_once(db_with_migrations: DatabasePool) -> None:
    from embry0.storage.repositories.jobs import JobsRepository

    jobs = JobsRepository(db_with_migrations)
    job_id = await jobs.create(repo="acme/app", task="t")
    await jobs.update(job_id, status="running")
    assert await jobs.account_waiting() == [], "a job that does not wait is not listed"

    wait = {
        "provider": "anthropic",
        "reason": "quota_exhausted",
        "until": "2030-01-01T00:00:00+00:00",
        "mode": "resume",
    }
    await jobs.update(job_id, status="paused", account_wait=wait)
    [row] = await jobs.account_waiting()
    assert row["job_id"] == job_id and row["account_wait"]["provider"] == "anthropic"

    assert await jobs.claim_account_wait(job_id) is True
    assert await jobs.claim_account_wait(job_id) is False, "a second taker gets nothing"
    stored = await jobs.get(job_id)
    assert stored is not None and stored["status"] == "running" and stored["account_wait"] is None
    assert await jobs.account_waiting() == []
