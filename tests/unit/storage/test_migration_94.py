"""Migration 94 — healing_cases.signature_source: how the watcher chose a case's signature (#657)."""

import asyncpg
import pytest

from embry0.services.healing.lifecycle import SignatureSource
from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def _sql_for(version: int) -> str:
    match = [sql for v, _, sql in MIGRATIONS if v == version]
    assert len(match) == 1, f"migration {version} must exist exactly once"
    return match[0]


def test_migration_94_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert 94 in versions, "migration 94 (healing_cases.signature_source, #657) is missing"
    assert versions == list(range(1, len(versions) + 1))


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_column_takes_the_vocabulary_and_nothing_else(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await db.execute(_sql_for(94))  # a second run must be a no-op

    async def insert(case_id: str, source: str | None) -> None:
        await db.execute(
            "INSERT INTO healing_cases (id, repo, origin, signature_source) VALUES ($1, 'owner/repo', 'watcher', $2)",
            case_id,
            source,
        )

    for index, member in enumerate(SignatureSource):
        await insert(f"hc-ok-{index}", member.value)
    await insert("hc-legacy", None)  # every case opened before the column

    with pytest.raises(asyncpg.CheckViolationError):
        await insert("hc-bad", "loudest")
