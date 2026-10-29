"""Migration 131 — the progression rollup's tables, and the two trace indexes its reads use (#176).

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS

#: This migration's number.
VERSION = 131


def _sql() -> str:
    return next(s for v, _, s in MIGRATIONS if v == VERSION)


def test_the_rollup_migration_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_130.py)."""
    assert MIGRATIONS[-1][0] == VERSION, f"migration {VERSION} must add the progression rollup"
    assert "CREATE TABLE IF NOT EXISTS progression_rollups" in _sql()


def test_the_indexes_are_plain_and_idempotent() -> None:
    """The runner wraps each migration in a transaction, where CREATE INDEX CONCURRENTLY cannot run."""
    sql = _sql()
    assert "CREATE INDEX IF NOT EXISTS idx_traces_created_at ON traces (created_at)" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_traces_success_agent" in sql
    assert "WHERE result = 'success' AND error_code IS NULL" in sql, "the partial index matches the runs' filter"
    assert "CONCURRENTLY" not in sql


async def _rollup(db: DatabasePool, pack_hash: str = "h1") -> None:
    now = datetime(2026, 3, 10, tzinfo=UTC)
    await db.execute(
        "INSERT INTO progression_rollups"
        " (pack_hash, fold_version, timezone, through_day, boundary, state, rewards, built_at, rebuilt_at)"
        " VALUES ($1, 'v1', 'UTC', $2, $3, '{}'::jsonb, '[]'::jsonb, $3, $3)",
        pack_hash,
        date(2026, 3, 7),
        now,
    )
    await db.execute(
        "INSERT INTO progression_daily (pack_hash, day, agent_type, skill, xp) VALUES ($1, $2, 'developer', 'coding', 60000)",
        pack_hash,
        date(2026, 3, 1),
    )
    await db.execute(
        "INSERT INTO progression_milestones (pack_hash, seq, at, kind, agent_type, skill, level)"
        " VALUES ($1, 0, $2, 'level', 'developer', 'coding', 40)",
        pack_hash,
        now,
    )


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_rollup_takes_its_rows_with_it(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _rollup(db, "h1")
    await _rollup(db, "h2")
    await db.execute("DELETE FROM progression_rollups WHERE pack_hash = 'h1'")
    daily = await db.fetch("SELECT pack_hash FROM progression_daily")
    milestones = await db.fetch("SELECT pack_hash FROM progression_milestones")
    assert [r["pack_hash"] for r in daily] == ["h2"] and [r["pack_hash"] for r in milestones] == ["h2"], (
        "deleting a rollup deletes its days and milestones, and no other's"
    )


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_milestone_is_a_level_a_character_level_or_a_rare(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _rollup(db)
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(
            "INSERT INTO progression_milestones (pack_hash, seq, at, kind, agent_type) VALUES ('h1', 1, NOW(), 'quest', 'x')"
        )
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "INSERT INTO progression_milestones (pack_hash, seq, at, kind, agent_type, rare)"
            " VALUES ('h1', 0, NOW(), 'rare', 'x', 'paper-crown')"
        )


async def _plan(db: DatabasePool, sql: str, *args: object) -> str:
    async with db.transaction() as conn:
        # Too few rows for the planner to prefer an index on its own: forbid the full scan to see what can serve.
        await conn.execute("SET LOCAL enable_seqscan = off")
        rows = await conn.fetch("EXPLAIN " + sql, *args)
    return "\n".join(str(r[0]) for r in rows)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_recent_events_and_the_narrowed_journal_reach_traces_through_an_index(
    db_with_migrations: DatabasePool,
) -> None:
    from embry0.storage.repositories.progression import _RUNS_SQL, events_sql

    db = db_with_migrations
    since = datetime(2026, 3, 8, tzinfo=UTC)
    plan = await _plan(db, events_sql(since=True), since)
    runs = next(line for line in plan.splitlines() if "on traces t" in line and "Scan" in line)
    assert "idx_traces_created_at" in runs or "idx_traces_success_agent" in runs, (
        f"the recent runs are not read through a created_at index:\n{plan}"
    )
    plan = await _plan(db, _RUNS_SQL.format(agents="AND t.agent_type = ANY($1::text[])"), ["review", "qa"])
    assert "idx_traces_success_agent" in plan, f"the journal's runs do not use the agent index:\n{plan}"
