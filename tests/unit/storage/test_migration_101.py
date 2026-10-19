"""Migration 101 — standards and repo_standard_state (ADR 0014)."""

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_101_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(101) == 1, "migration 101 (standards, repo_standard_state) must exist exactly once"
    assert versions == list(range(1, len(versions) + 1))
    description = next(d for v, d, _ in MIGRATIONS if v == 101)
    assert "standards" in description and "repo_standard_state" in description


def test_migration_101_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_100.py)."""
    assert MIGRATIONS[-1][0] == 101, "migration 101 must add standards and repo_standard_state"


@pytest.mark.requires_postgres
async def test_one_active_version_per_owner_and_kind(db_with_migrations: DatabasePool) -> None:
    insert = "INSERT INTO standards (id, owner, kind, version, status) VALUES ($1, 'acme', 'design', $2, $3)"
    await db_with_migrations.execute(insert, "std-1", 1, "active")
    await db_with_migrations.execute(insert, "std-2", 2, "superseded")
    with pytest.raises(asyncpg.UniqueViolationError):
        await db_with_migrations.execute(insert, "std-3", 3, "active")
    with pytest.raises(asyncpg.UniqueViolationError):
        await db_with_migrations.execute(insert, "std-4", 1, "draft")


@pytest.mark.requires_postgres
@pytest.mark.parametrize(
    ("column", "value"),
    [("kind", "'layout'"), ("status", "'live'"), ("schema", "'[]'::jsonb"), ("values", "'\"x\"'::jsonb")],
)
async def test_standards_checks_refuse_a_bad_row(db_with_migrations: DatabasePool, column: str, value: str) -> None:
    row = {"kind": "'design'", "status": "'active'", "schema": "'{}'::jsonb", "values": "'{}'::jsonb", column: value}
    with pytest.raises(asyncpg.CheckViolationError):
        await db_with_migrations.execute(
            "INSERT INTO standards (id, owner, version, kind, status, schema, values) "
            f"VALUES ('std-x', 'acme', 1, {row['kind']}, {row['status']}, {row['schema']}, {row['values']})"
        )


@pytest.mark.requires_postgres
async def test_repo_state_defaults_to_off_and_bounds_the_score(db_with_migrations: DatabasePool) -> None:
    await db_with_migrations.execute(
        "INSERT INTO repo_standard_state (repo, owner, kind) VALUES ('acme/widgets', 'acme', 'design')"
    )
    row = await db_with_migrations.fetchrow("SELECT mode, score, findings_open, exceptions FROM repo_standard_state")
    assert row is not None
    assert (row["mode"], row["score"], row["findings_open"], row["exceptions"]) == ("off", None, 0, [])
    for update in ("score = 101", "score = -1", "mode = 'strict'", "findings_open = -1"):
        with pytest.raises(asyncpg.CheckViolationError):
            await db_with_migrations.execute(f"UPDATE repo_standard_state SET {update}")
