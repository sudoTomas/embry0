"""Migration 128 — config_revisions, every change to a settings document."""

from __future__ import annotations

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_128_is_present_once() -> None:
    assert [v for v, _, _ in MIGRATIONS].count(128) == 1, "migration 128 must create config_revisions"


@pytest.mark.requires_postgres
async def test_a_revision_takes_only_a_known_surface_and_source(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    insert = "INSERT INTO config_revisions (scope, surface, source) VALUES ('deployment', $1, $2)"
    for source in ("person", "file", "bootstrap", "restore", "upgrade"):
        await db.execute(insert, "settings", source)
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(insert, "settings", "robot")
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(insert, "qa_config", "person")
    indexes = await db.fetch("SELECT indexname FROM pg_indexes WHERE tablename = 'config_revisions'")
    assert "idx_config_revisions_scope" in {r["indexname"] for r in indexes}
