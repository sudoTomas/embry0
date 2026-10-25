"""Migration 110 — leads: work embry0 finds by itself, answered like every proposal."""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_110_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(110) == 1, "migration 110 (leads) must exist exactly once"
    assert versions == list(range(1, len(versions) + 1))
    assert "leads" in next(d for v, d, _ in MIGRATIONS if v == 110)


def test_migration_110_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_109.py)."""
    assert MIGRATIONS[-1][0] == 110, "migration 110 must add leads"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_leads_are_stored_and_answered_like_every_proposal(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await db.execute(
        "INSERT INTO leads (id, kind, repo, source_key, title) VALUES ('ci:abc', 'ci', 'o/r', 'check:build', 'Build fails')"
    )
    with pytest.raises(Exception, match="check"):
        await db.execute(
            "INSERT INTO leads (id, kind, repo, source_key, title) VALUES ('x:1', 'rumor', 'o/r', 'k', 't')"
        )
    with pytest.raises(Exception, match="check"):
        await db.execute(
            "INSERT INTO leads (id, kind, repo, source_key, title, severity) VALUES ('ci:2', 'ci', 'o/r', 'k', 't', 'dire')"
        )
    await db.execute(
        "INSERT INTO proposal_decisions (proposal_id, repo, kind, decision, decided_by) VALUES ('ci:abc', 'o/r', 'ci', 'accept', 'octo')"
    )
    row = await db.fetchrow("SELECT state, effort, data FROM leads WHERE id = 'ci:abc'")
    assert row["state"] == "open" and row["effort"] == "medium"
