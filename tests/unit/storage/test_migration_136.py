"""Migration 136 — when a repository left a background feature's scope (#165).

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS

#: This migration's number.
VERSION = 136
AT = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def _sql() -> str:
    return next((s for v, _, s in MIGRATIONS if v == VERSION), "")


def _exits(db: DatabasePool) -> Any:
    try:
        module = importlib.import_module("embry0.storage.repositories.background_scope")
    except ModuleNotFoundError:
        pytest.fail("embry0.storage.repositories.background_scope records when a repository left a feature's scope")
    return module.ScopeExitsRepository(db)


def test_the_scope_exits_migration_is_listed_once() -> None:
    """The "last entry" assertion passed to tests/unit/storage/test_migration_137.py."""
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


@pytest.mark.requires_postgres
async def test_a_repository_that_left_is_recorded_once_and_forgotten_when_it_comes_back(
    db_with_migrations: DatabasePool,
) -> None:
    assert _sql(), f"migration {VERSION} creates background_scope_exits"
    exits = _exits(db_with_migrations)
    assert await exits.mark("slop_meter", ["Acme/Widgets", "acme/gadgets"], at=AT) == ["acme/gadgets", "acme/widgets"]
    later = AT + timedelta(days=2)
    assert await exits.mark("slop_meter", ["acme/widgets", "other/kit"], at=later) == ["other/kit"], (
        "a repository already recorded keeps the time it first left"
    )
    assert await exits.left("slop_meter") == {"acme/gadgets": AT, "acme/widgets": AT, "other/kit": later}
    assert await exits.left("scout") == {}, "each feature keeps its own record"
    assert await exits.clear("slop_meter", ["ACME/WIDGETS", "ghost/none"]) == ["acme/widgets"]
    assert set(await exits.left("slop_meter")) == {"acme/gadgets", "other/kit"}


@pytest.mark.requires_postgres
async def test_a_slug_is_stored_lowercased_and_running_it_again_keeps_the_rows(
    db_with_migrations: DatabasePool,
) -> None:
    assert _sql(), f"migration {VERSION} creates background_scope_exits"
    db = db_with_migrations
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute("INSERT INTO background_scope_exits (feature, repo) VALUES ('slop_meter', 'Acme/Widgets')")
    await db.execute("INSERT INTO background_scope_exits (feature, repo) VALUES ('slop_meter', 'acme/widgets')")
    await db.execute(_sql())
    assert await db.fetchval("SELECT count(*) FROM background_scope_exits") == 1, "the migration is idempotent"
