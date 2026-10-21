"""Migration 105 — repo_setup_proposals, agent-proposed test setups a person applies (ADR 0019)."""

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS, run_migrations


def _sql_for(version: int) -> str:
    match = [sql for v, _, sql in MIGRATIONS if v == version]
    assert len(match) == 1, f"migration {version} must exist exactly once"
    return match[0]


def test_migration_105_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(105) == 1, "migration 105 (repo_setup_proposals) must exist exactly once"
    assert versions == list(range(1, len(versions) + 1))
    description = next(d for v, d, _ in MIGRATIONS if v == 105)
    assert "repo_setup_proposals" in description


@pytest.mark.requires_postgres
async def test_a_fresh_schema_has_the_table_with_its_defaults(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    assert await db.fetchval("SELECT 1 FROM embry0_migrations WHERE version = 105") == 1
    await db.execute("INSERT INTO repo_setup_proposals (id, repo, status) VALUES ('prop-1', 'acme/widgets', 'running')")
    row = await db.fetchrow("SELECT * FROM repo_setup_proposals WHERE id = 'prop-1'")
    assert row is not None
    assert (row["notes_md"], row["failure"], row["created_by"]) == ("", "", "")
    assert (row["job_id"], row["proposed_yaml"], row["smoke"], row["decided_by"], row["decided_at"]) == (
        None,
        None,
        None,
        None,
        None,
    )
    assert row["created_at"] is not None and row["updated_at"] is not None


@pytest.mark.requires_postgres
async def test_it_applies_on_a_schema_at_104(db_with_migrations: DatabasePool) -> None:
    """A deployment at 104 gains the table on its next start; running the SQL again changes nothing."""
    db = db_with_migrations
    await db.execute("DROP TABLE repo_setup_proposals")
    await db.execute("DELETE FROM embry0_migrations WHERE version = 105")
    assert await db.fetchval("SELECT 1 FROM embry0_migrations WHERE version = 105") is None

    await run_migrations(db)
    await db.execute(_sql_for(105))

    assert await db.fetchval("SELECT to_regclass('repo_setup_proposals') IS NOT NULL")
    assert await db.fetchval("SELECT 1 FROM embry0_migrations WHERE version = 105") == 1


@pytest.mark.requires_postgres
async def test_the_status_takes_the_vocabulary_and_nothing_else(db_with_migrations: DatabasePool) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await db_with_migrations.execute(
            "INSERT INTO repo_setup_proposals (id, repo, status) VALUES ('prop-1', 'acme/widgets', 'draft')"
        )


@pytest.mark.requires_postgres
async def test_one_running_or_pending_proposal_per_repository(db_with_migrations: DatabasePool) -> None:
    """The partial unique index is the lock: finished rows never count."""
    db = db_with_migrations
    insert = "INSERT INTO repo_setup_proposals (id, repo, status) VALUES ($1, $2, $3)"
    for n, status in enumerate(("applied", "rejected", "stale", "failed")):
        await db.execute(insert, f"prop-done-{n}", "acme/widgets", status)
    await db.execute(insert, "prop-live", "acme/widgets", "running")
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(insert, "prop-second", "acme/widgets", "pending")
    await db.execute(insert, "prop-other", "acme/gadgets", "pending")
