"""Migration 130 — the progression content pack an operator edits (#176): versions, one draft, one active."""

from __future__ import annotations

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_130_is_present_once() -> None:
    """The last-entry assertion moved to tests/unit/storage/test_migration_131.py."""
    assert [v for v, _, _ in MIGRATIONS].count(130) == 1, "migration 130 must create progression_packs, once"


_INSERT = (
    "INSERT INTO progression_packs (status, schema_version, follows_default, text, document, source) "
    "VALUES ($1, 1, $2, $3, $4::jsonb, $5) RETURNING version"
)


@pytest.mark.requires_postgres
async def test_one_active_version_and_one_draft_at_most(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    first = await db.fetchval(_INSERT, "active", False, "version: 1\n", {"version": 1}, "person")
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.fetchval(_INSERT, "active", True, None, None, "reset")
    await db.fetchval(_INSERT, "draft", False, "version: 1\n", {"version": 1}, "person")
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.fetchval(_INSERT, "draft", False, "version: 1\n", {"version": 1}, "person")
    later = await db.fetchval(_INSERT, "superseded", True, None, None, "reset")
    assert later > first, "a version number is never reused: each row takes the next"


@pytest.mark.requires_postgres
async def test_a_version_holds_its_text_or_follows_the_default_and_names_a_known_source(
    db_with_migrations: DatabasePool,
) -> None:
    db = db_with_migrations
    with pytest.raises(asyncpg.CheckViolationError):
        await db.fetchval(_INSERT, "superseded", False, None, None, "person")  # customised, but no text
    with pytest.raises(asyncpg.CheckViolationError):
        await db.fetchval(_INSERT, "superseded", True, "x: 1\n", {"x": 1}, "reset")  # follows, yet holds a text
    with pytest.raises(asyncpg.CheckViolationError):
        await db.fetchval(_INSERT, "superseded", False, "x\n", ["x"], "person")  # document not an object
    with pytest.raises(asyncpg.CheckViolationError):
        await db.fetchval(_INSERT, "superseded", True, None, None, "bootstrap")
    for source in ("person", "file", "restore", "reset", "upgrade"):
        await db.fetchval(_INSERT, "superseded", True, None, None, source)
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(
            "INSERT INTO progression_packs (status, schema_version, follows_default, source, parent_version) "
            "VALUES ('superseded', 1, TRUE, 'reset', -1)"
        )
    parent = await db.fetchval(
        "INSERT INTO progression_packs (status, schema_version, follows_default, source) "
        "VALUES ('superseded', 1, TRUE, 'reset') RETURNING parent_version"
    )
    assert parent == 0, "a row records the active version it was written over, 0 for none"
    await db.execute("INSERT INTO progression_pack_defaults (hash, text) VALUES ('h', 'version: 1\n')")
    assert await db.fetchval("SELECT text FROM progression_pack_defaults WHERE hash = 'h'") == "version: 1\n"
