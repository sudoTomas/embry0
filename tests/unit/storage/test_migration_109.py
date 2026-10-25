"""Migration 109 — jobs.area: the part of a monorepo a job's pull request changes."""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_109_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(109) == 1, "migration 109 (jobs.area) must exist exactly once"
    assert versions == list(range(1, len(versions) + 1))
    assert "area" in next(d for v, d, _ in MIGRATIONS if v == 109)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_job_has_no_area_until_one_is_recorded(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    column = await db.fetchrow(
        "SELECT data_type, is_nullable FROM information_schema.columns WHERE table_name = 'jobs' AND column_name = 'area'"
    )
    assert column is not None and column["data_type"] == "text" and column["is_nullable"] == "YES"
