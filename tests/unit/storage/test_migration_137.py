"""Migration 137 — when the asks feed first handed an ask out (#295).

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS

#: This migration's number.
VERSION = 137


def _sql() -> str:
    return next((s for v, _, s in MIGRATIONS if v == VERSION), "")


def test_the_handed_out_migration_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_136.py)."""
    assert MIGRATIONS[-1][0] == VERSION, f"migration {VERSION} must add asks.handed_out_at"
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


@pytest.mark.requires_postgres
async def test_asks_carry_when_they_were_first_handed_out(db_with_migrations: DatabasePool) -> None:
    assert "handed_out_at" in _sql(), f"migration {VERSION} adds asks.handed_out_at"
    column = await db_with_migrations.fetchrow(
        "SELECT data_type, is_nullable FROM information_schema.columns WHERE table_name = 'asks' AND column_name = 'handed_out_at'"
    )
    assert (
        column is not None and column["data_type"] == "timestamp with time zone" and column["is_nullable"] == "YES"
    ), f"asks.handed_out_at is a nullable timestamp: none until the feed hands the ask out: {column}"
    await db_with_migrations.execute(_sql())  # again: a migration applied twice changes nothing
