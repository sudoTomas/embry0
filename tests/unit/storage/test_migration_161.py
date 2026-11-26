"""Migration 161: ``provider_config.local_max_runs`` leaves for the settings document's ``local_models.max_runs``
(#174).

SQL cannot write the settings document (its text is the operator's YAML), so a stored value other than the default
(1) waits in ``settings_carried`` for the first start, which writes it through the store
(``embry0.settings.carried``); the column goes. The number is ``VERSION`` alone: renumbering the migration changes
that line and this file's name.
"""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS, run_migrations

#: This migration's number.
VERSION = 161


def test_the_local_max_runs_migration_is_listed_once() -> None:
    """The last-entry assertion moved to tests/unit/storage/test_migration_162.py."""
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


async def _before(db: DatabasePool, runs: int | None) -> None:
    """The table as the release before left it, with ``runs`` stored (None: no row at all)."""
    await db.execute("DELETE FROM embry0_migrations WHERE version = $1", VERSION)
    await db.execute("DROP TABLE IF EXISTS settings_carried")
    await db.execute("ALTER TABLE provider_config ADD COLUMN IF NOT EXISTS local_max_runs INTEGER NOT NULL DEFAULT 1")
    await db.execute("DELETE FROM provider_config")
    if runs is not None:
        await db.execute("INSERT INTO provider_config (id, local_max_runs) VALUES ('default', $1)", runs)


async def _column(db: DatabasePool) -> bool:
    return bool(
        await db.fetchval(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = 'provider_config' AND column_name = 'local_max_runs'"
        )
    )


@pytest.mark.requires_postgres
async def test_a_stored_value_waits_for_the_first_start_and_the_column_goes(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _before(db, 3)
    await run_migrations(db)
    carried = [dict(r) for r in await db.fetch("SELECT key, value, from_where FROM settings_carried")]
    assert carried == [{"key": "local_models.max_runs", "value": 3, "from_where": "provider_config.local_max_runs"}], (
        f"the stored value must wait for the start that writes it into the document: {carried}"
    )
    assert not await _column(db), "provider_config.local_max_runs is still there"


@pytest.mark.requires_postgres
@pytest.mark.parametrize("runs", [1, None], ids=["the-default", "no-row"])
async def test_nothing_waits_when_nothing_differs_from_the_default(
    db_with_migrations: DatabasePool, runs: int | None
) -> None:
    db = db_with_migrations
    await _before(db, runs)
    await run_migrations(db)
    assert await db.fetch("SELECT key FROM settings_carried") == [], "the section's own default was carried"
    assert not await _column(db)


@pytest.mark.requires_postgres
async def test_the_migration_runs_again_on_a_table_without_the_column(db_with_migrations: DatabasePool) -> None:
    """Applied twice (a ledger restored from before it), it finds no column and carries nothing."""
    db = db_with_migrations
    await db.execute("DELETE FROM embry0_migrations WHERE version = $1", VERSION)
    await run_migrations(db)
    assert await db.fetch("SELECT key FROM settings_carried") == []
