"""Migration 149 — the progression rollup kept per scope, in new tables beside the previous release's.

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS, run_migrations

#: This migration's number.
VERSION = 149

AT = datetime(2026, 10, 30, tzinfo=UTC)
DAY = date(2026, 10, 29)

#: Migration 131's tables, the previous release's rollup, as that release reads and writes them.
OLD = ("progression_rollups", "progression_daily", "progression_milestones")
NEW = ("progression_scope_rollups", "progression_scope_daily", "progression_scope_milestones")


def test_the_rollup_scope_migration_is_listed_once() -> None:
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


async def _keys(db: DatabasePool, table: str) -> list[str]:
    rows = await db.fetch(
        "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
        "WHERE i.indrelid = $1::regclass AND i.indisprimary ORDER BY array_position(i.indkey, a.attnum)",
        table,
    )
    return [str(r["attname"]) for r in rows]


async def _columns(db: DatabasePool, table: str) -> list[tuple[str, str, bool]]:
    rows = await db.fetch(
        "SELECT column_name, data_type, is_nullable = 'YES' AS nullable FROM information_schema.columns "
        "WHERE table_name = $1 ORDER BY ordinal_position",
        table,
    )
    return [(str(r["column_name"]), str(r["data_type"]), bool(r["nullable"])) for r in rows]


async def _write_as_the_previous_release(db: DatabasePool, pack_hash: str) -> None:
    """What the release before this one writes when it builds and advances its rollup: rows keyed by pack hash."""
    async with db.transaction() as conn:
        await conn.execute("DELETE FROM progression_rollups")
        await conn.execute(
            "INSERT INTO progression_rollups (pack_hash, fold_version, timezone, through_day, boundary, state, "
            "rewards, built_at, rebuilt_at) VALUES ($1, 'old', 'UTC', $2, $3, '{}', '[]', $3, $3)",
            pack_hash,
            DAY,
            AT,
        )
        for _ in range(2):
            await conn.execute(
                "INSERT INTO progression_daily (pack_hash, day, agent_type, skill, xp) "
                "VALUES ($1, $2, 'developer', 'coding', 10) "
                "ON CONFLICT (pack_hash, day, agent_type, skill) DO UPDATE SET xp = progression_daily.xp + EXCLUDED.xp",
                pack_hash,
                DAY,
            )
        await conn.execute(
            "INSERT INTO progression_milestones (pack_hash, seq, at, kind, agent_type, skill, level) "
            "VALUES ($1, 0, $2, 'level', 'developer', 'coding', 2)",
            pack_hash,
            AT,
        )


@pytest.mark.requires_postgres
async def test_each_new_table_is_kept_per_scope(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    assert await _keys(db, "progression_scope_rollups") == ["scope", "pack_hash"]
    assert await _keys(db, "progression_scope_daily") == ["scope", "pack_hash", "day", "agent_type", "skill"]
    assert await _keys(db, "progression_scope_milestones") == ["scope", "pack_hash", "seq"]
    head = (
        "INSERT INTO progression_scope_rollups (scope, pack_hash, repos, fold_version, timezone, through_day, "
        "boundary, state, rewards, built_at, rebuilt_at) VALUES ($1, 'p', $2, 'new', 'UTC', $3, $4, '{}', '[]', $4, $4)"
    )
    await db.execute(head, "", None, DAY, AT)
    await db.execute(head, "repos:x", ["acme/web"], DAY, AT)
    for scope, repos in (("repos:y", None), ("", ["acme/web"])):
        with pytest.raises(Exception, match="check"):
            await db.execute(head, scope, repos, DAY, AT)
    await db.execute("INSERT INTO progression_scope_daily VALUES ('repos:x', 'p', $1, 'developer', 'coding', 10)", DAY)
    await db.execute("DELETE FROM progression_scope_rollups WHERE scope = 'repos:x'")
    assert await db.fetchval("SELECT count(*) FROM progression_scope_daily") == 0, "a scope's rows go with its head"


@pytest.mark.requires_postgres
@pytest.mark.changes_schema
async def test_the_previous_release_keeps_its_rollup_and_writes_it_after_the_upgrade(
    db_with_migrations: DatabasePool,
) -> None:
    """A rollback to the release before this one finds migration 131's tables as it left them and writes them as
    it always did; this release's tables are others."""
    db = db_with_migrations
    shapes = {table: (await _columns(db, table), await _keys(db, table)) for table in OLD}
    assert shapes["progression_rollups"][1] == ["pack_hash"], "migration 131's rollup is keyed by pack hash alone"
    await db.execute("DELETE FROM embry0_migrations WHERE version = $1", VERSION)
    for table in NEW[::-1]:
        await db.execute(f"DROP TABLE {table}")
    await _write_as_the_previous_release(db, "p-before")
    await run_migrations(db)

    assert {table: (await _columns(db, table), await _keys(db, table)) for table in OLD} == shapes, (
        "the upgrade leaves migration 131's tables as they were"
    )
    assert await db.fetchval("SELECT pack_hash FROM progression_rollups") == "p-before", "and their rows"
    assert await db.fetchval("SELECT xp FROM progression_daily") == 20
    await _write_as_the_previous_release(db, "p-after")
    assert await db.fetchval("SELECT pack_hash FROM progression_rollups") == "p-after"
    assert await db.fetchval("SELECT count(*) FROM progression_scope_rollups") == 0, "this release's are its own"
