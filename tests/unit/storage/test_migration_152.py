"""Migration 152 — the provider catalogue's stored overlay (ADR 0031): one row, and its revisions.

``provider_catalogue`` holds the deployment's overlay of the shipped catalogue, one row whose scope is
``deployment``, inserted by the migration so a store's lock always has a row to take. Its changes are
``config_revisions`` rows of their own surface. The number is ``VERSION`` alone: renumbering the migration changes
that line and this file's name.
"""

from __future__ import annotations

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS, run_migrations

#: This migration's number.
VERSION = 152


def test_the_provider_catalogue_migration_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_151.py)."""
    assert MIGRATIONS[-1][0] == VERSION, f"migration {VERSION} must store the provider catalogue"
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


@pytest.mark.requires_postgres
async def test_it_stores_one_empty_overlay_and_takes_no_other_row(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    rows = [
        dict(r)
        for r in await db.fetch("SELECT scope, yaml_text, data, generation, imported, shipped FROM provider_catalogue")
    ]
    assert rows == [
        {"scope": "deployment", "yaml_text": "", "data": {}, "generation": 1, "imported": [], "shipped": {}}
    ], rows
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute("INSERT INTO provider_catalogue (scope) VALUES ('repository:acme/app')")
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute("UPDATE provider_catalogue SET data = '[]'::jsonb")
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute("UPDATE provider_catalogue SET shipped = '[]'::jsonb")


@pytest.mark.requires_postgres
async def test_its_changes_are_revisions_of_their_own_surface(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    insert = (
        "INSERT INTO config_revisions (scope, surface, before, after, actor, source) "
        "VALUES ('deployment', $1, '{}'::jsonb, '{}'::jsonb, 'a', 'person')"
    )
    for surface in ("settings", "provider_catalogue"):
        await db.execute(insert, surface)
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(insert, "catalogue")


@pytest.mark.requires_postgres
async def test_it_runs_again_over_a_stored_overlay_and_keeps_it(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await db.execute("UPDATE provider_catalogue SET yaml_text = 'kept', generation = 5")
    await db.execute("DELETE FROM embry0_migrations WHERE version = $1", VERSION)
    await run_migrations(db)
    assert [tuple(r) for r in await db.fetch("SELECT yaml_text, generation FROM provider_catalogue")] == [("kept", 5)]
