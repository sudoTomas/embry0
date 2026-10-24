"""Migration 108 — owner proposals: repo owners, the answers to proposals, the propose key scope."""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_108_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(108) == 1, "migration 108 (owner proposals) must exist exactly once"
    assert versions == list(range(1, len(versions) + 1))
    assert "proposals" in next(d for v, d, _ in MIGRATIONS if v == 108)


def test_migration_108_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_107.py)."""
    assert MIGRATIONS[-1][0] == 108, "migration 108 must add owner proposals"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_answers_and_the_propose_scope_are_stored(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await db.execute(
        "INSERT INTO proposal_decisions (proposal_id, repo, kind, decision, decided_by) VALUES ('issue:i-1', 'o/r', 'issue', 'decline', 'octo')"
    )
    with pytest.raises(Exception, match="check"):
        await db.execute(
            "INSERT INTO proposal_decisions (proposal_id, repo, kind, decision, decided_by) VALUES ('x', 'o/r', 'issue', 'maybe', 'octo')"
        )
    await db.execute(
        "INSERT INTO api_keys (id, name, key_hash, prefix, scope) VALUES ('k-1', 'assistant', 'h', 'p', 'propose')"
    )
    owners = await db.fetchval(
        "SELECT data_type FROM information_schema.columns WHERE table_name = 'repo_preferences' AND column_name = 'owners'"
    )
    assert owners == "jsonb"
