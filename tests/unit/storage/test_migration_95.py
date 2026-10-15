"""Migration 95 — pr_reviews and repo_preferences.pr_review (#30)."""

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS

SHA = "a" * 40


def _sql_for(version: int) -> str:
    match = [sql for v, _, sql in MIGRATIONS if v == version]
    assert len(match) == 1, f"migration {version} must exist exactly once"
    return match[0]


def test_migration_95_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert 95 in versions, "migration 95 (pr_reviews, #30) is missing"
    assert versions == list(range(1, len(versions) + 1))


def test_migration_95_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_94.py)."""
    assert MIGRATIONS[-1][0] == 95, "migration 95 must add pr_reviews"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_trigger_key_is_stored_once(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await db.execute(_sql_for(95))  # a second run must be a no-op
    insert = (
        "INSERT INTO pr_reviews (id, repo, number, head_sha, trigger, trigger_key) "
        "VALUES ($1, 'acme/widgets', 7, $2, 'ready', $3)"
    )
    key = f"ready:acme/widgets#7@{SHA}"
    await db.execute(insert, "prr-1", SHA, key)
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(insert, "prr-2", SHA, key)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_trigger_takes_the_vocabulary_and_nothing_else(db_with_migrations: DatabasePool) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await db_with_migrations.execute(
            "INSERT INTO pr_reviews (id, repo, number, head_sha, trigger, trigger_key) "
            "VALUES ('prr-x', 'acme/widgets', 7, 'abc1234', 'push', 'push:1')"
        )


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_repo_preferences_gains_a_nullable_pr_review_column(db_with_migrations: DatabasePool) -> None:
    row = await db_with_migrations.fetchrow(
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_name = 'repo_preferences' AND column_name = 'pr_review'"
    )
    assert row is not None, "repo_preferences.pr_review is missing"
    assert (row["data_type"], row["is_nullable"]) == ("jsonb", "YES")
