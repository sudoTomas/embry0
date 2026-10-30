"""Migration 134 — a settings layer per organization and per repository (spec 2026-10-29, #161).

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS

#: This migration's number.
VERSION = 134

_CHECK = (
    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
    "WHERE conrelid = 'settings_documents'::regclass AND contype = 'c' AND conname LIKE '%scope%'"
)


def _sql() -> str:
    return next((s for v, _, s in MIGRATIONS if v == VERSION), "")


def test_the_settings_layers_migration_is_listed_once() -> None:
    """The "last entry" assertion passed to tests/unit/storage/test_migration_135.py."""
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


@pytest.mark.requires_postgres
@pytest.mark.parametrize(
    "scope",
    ["organization:acme", "organization:my-org.io", "repository:acme/widgets", "repository:group/sub/project"],
)
async def test_a_layer_can_be_stored_under_its_scope(db_with_migrations: DatabasePool, scope: str) -> None:
    await db_with_migrations.execute("INSERT INTO settings_documents (scope, generation) VALUES ($1, 1)", scope)
    assert await db_with_migrations.fetchval("SELECT count(*) FROM settings_documents WHERE scope = $1", scope) == 1


@pytest.mark.requires_postgres
@pytest.mark.parametrize(
    "scope",
    ["organization:Acme", "organization:acme/widgets", "organization:", "repository:acme", "team:acme", "acme"],
)
async def test_anything_else_is_refused(db_with_migrations: DatabasePool, scope: str) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await db_with_migrations.execute("INSERT INTO settings_documents (scope) VALUES ($1)", scope)


@pytest.mark.requires_postgres
async def test_the_deployment_document_is_untouched_and_the_check_is_one(db_with_migrations: DatabasePool) -> None:
    """Running it again over a written document keeps the row as it was, and leaves one scope check."""
    db = db_with_migrations
    await db.execute("UPDATE settings_documents SET yaml_text = 'asks: {}', generation = 7 WHERE scope = 'deployment'")
    before = dict(await db.fetchrow("SELECT * FROM settings_documents WHERE scope = 'deployment'"))
    await db.execute(_sql())
    after = dict(await db.fetchrow("SELECT * FROM settings_documents WHERE scope = 'deployment'"))
    assert after == before, "the deployment's row keeps its text, values and generation, so its ETag stays valid"
    checks = [str(r[0]) for r in await db.fetch(_CHECK)]
    assert len(checks) == 1 and "organization:" in checks[0], f"one scope check, the widened one: {checks}"
