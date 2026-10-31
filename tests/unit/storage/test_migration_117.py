"""Migration 117 — proposal briefs: each owner proposal in plain words."""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_117_present() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(117) == 1, "migration 117 (proposal briefs) must exist exactly once"
    sql = next(s for v, _, s in MIGRATIONS if v == 117)
    assert "proposal_briefs" in sql and "input_hash" in sql


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_brief_is_stored_replaced_and_pruned(db_with_migrations: DatabasePool) -> None:
    from embry0.services.proposal_briefs import Brief
    from embry0.storage.repositories.proposal_briefs import ProposalBriefsRepository

    briefs = ProposalBriefsRepository(db_with_migrations)
    await briefs.save(
        "case:1",
        "h1",
        Brief("Stop the retries", "It retries.", "Stop it; you get a PR.", "small"),
        model="m",
        prompt_hash="p1",
    )
    await briefs.save("case:2", "h2", Brief("Other", "P.", "F.", "large"), model="m", prompt_hash="p1")
    await briefs.save(
        "case:1", "h3", Brief("Stop the retries now", "It retries.", "Stop it.", "medium"), model="m2", prompt_hash="p1"
    )
    stored = await briefs.all()
    assert stored["case:1"]["input_hash"] == "h3" and stored["case:1"]["size"] == "medium"
    assert stored["case:1"]["model"] == "m2" and stored["case:1"]["written_at"] is not None

    assert await briefs.prune({"case:1"}) == 1
    assert set(await briefs.all()) == {"case:1"}
    assert await briefs.prune(set()) == 1, "nothing asked keeps nothing"
