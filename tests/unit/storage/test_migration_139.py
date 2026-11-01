"""Migration 139 — ``asks.lease_until``: until when an ask the feed handed out is held for the poll that took it
(#293).

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS

#: This migration's number.
VERSION = 139


def test_the_lease_migration_is_listed_once() -> None:
    """The "last entry" assertion passed to tests/unit/storage/test_migration_140.py."""
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


@pytest.mark.requires_postgres
async def test_an_ask_carries_until_when_its_hand_out_is_leased(db_with_migrations: DatabasePool) -> None:
    column = await db_with_migrations.fetchrow(
        "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_name = 'asks' AND column_name = 'lease_until'"
    )
    assert column is not None, f"migration {VERSION} adds asks.lease_until"
    assert (column["data_type"], column["is_nullable"], column["column_default"]) == (
        "timestamp with time zone",
        "YES",
        None,
    ), "an ask stored before the column, or never handed out, holds no lease"
