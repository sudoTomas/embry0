"""Migration 112 — job_ratings: the player's evaluation of a finished quest."""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_112_present() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(112) == 1, "migration 112 (job_ratings) must exist exactly once"
    assert "job_ratings" in next(d for v, d, _ in MIGRATIONS if v == 112)


def test_migration_112_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_111.py)."""
    assert MIGRATIONS[-1][0] == 112, "migration 112 must add job_ratings"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_rating_and_a_verdict_are_stored_once_per_job(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await db.execute("INSERT INTO job_ratings (job_id, rating, rated_by) VALUES ('job-1', 'good', 'octo')")
    with pytest.raises(Exception, match="check"):
        await db.execute("INSERT INTO job_ratings (job_id, rating, rated_by) VALUES ('job-2', 'great', 'octo')")
    with pytest.raises(Exception, match="duplicate|unique"):
        await db.execute("INSERT INTO job_ratings (job_id, verdict, rated_by) VALUES ('job-1', 'merged', 'octo')")
