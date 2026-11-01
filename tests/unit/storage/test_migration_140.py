"""Migration 140 — the API key that created a credential.

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS, run_migrations

#: This migration's number.
VERSION = 140


def test_the_creating_key_migration_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_139.py)."""
    assert MIGRATIONS[-1][0] == VERSION, f"migration {VERSION} must add credentials.created_by_key_id"
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


@pytest.mark.requires_postgres
async def test_a_credential_made_before_the_column_has_no_creating_key(db_with_migrations: DatabasePool) -> None:
    """A row from before the migration keeps no key id: none is back-filled from its ``created_by`` name."""
    db = db_with_migrations
    column = await db.fetchrow(
        "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_name = 'credentials' AND column_name = 'created_by_key_id'"
    )
    assert column is not None, f"migration {VERSION} adds credentials.created_by_key_id"
    assert (column["data_type"], column["is_nullable"], column["column_default"]) == ("text", "YES", None), (
        "a credential a session, the master key or the importer made has none (NULL)"
    )
    await db.execute("ALTER TABLE credentials DROP COLUMN created_by_key_id")
    await db.execute("DELETE FROM embry0_migrations WHERE version = $1", VERSION)
    await db.execute(
        "INSERT INTO credentials (id, kind, provider, host, label, secret_ciphertext, masked_preview, created_by) "
        "VALUES ('cred-000000000001', 'forge_token', 'github', 'github.com', 'before', 'x', '****', 'key:bot')"
    )
    await run_migrations(db)
    kept = await db.fetchval("SELECT created_by_key_id FROM credentials WHERE id = 'cred-000000000001'")
    assert kept is None, f"a credential made before migration {VERSION} names no creating key: {kept!r}"
