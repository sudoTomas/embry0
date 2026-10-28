"""Migration 129 — settings_documents, the deployment's operator policy."""

from __future__ import annotations

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_129_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_127.py)."""
    assert MIGRATIONS[-1][0] == 129, "migration 129 must create settings_documents"


@pytest.mark.requires_postgres
async def test_the_deployment_document_exists_empty_and_only_it(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    rows = await db.fetch(
        "SELECT scope, yaml_text, data, schema_version, generation, bootstrapped_keys FROM settings_documents"
    )
    assert [dict(r) for r in rows] == [
        {
            "scope": "deployment",
            "yaml_text": "",
            "data": {},
            "schema_version": 1,
            "generation": 1,
            "bootstrapped_keys": [],
        }
    ], "the row exists from the first boot, so a reader never meets a missing one"
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute("INSERT INTO settings_documents (scope) VALUES ('acme')")
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute("UPDATE settings_documents SET data = '[]'::jsonb")


@pytest.mark.requires_postgres
async def test_running_it_again_keeps_the_stored_document(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await db.execute("UPDATE settings_documents SET yaml_text = 'kept: 1', generation = 5")
    sql = next(s for v, _, s in MIGRATIONS if v == 129)
    await db.execute(sql)
    row = await db.fetchrow("SELECT yaml_text, generation FROM settings_documents")
    assert (row["yaml_text"], row["generation"]) == ("kept: 1", 5)
