"""Migration 111 — what the last analysis found in a log signature (watcher efficiency, spec 2026-10-25)."""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_111_present() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(111) == 1, "migration 111 (log_signatures analysis memory) must exist exactly once"
    assert "analysed_at" in next(sql for v, _, sql in MIGRATIONS if v == 111)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_analysis_columns_exist_and_start_empty(db_with_migrations: DatabasePool) -> None:
    columns = {
        r["column_name"]: r["data_type"]
        for r in await db_with_migrations.fetch(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'log_signatures'"
        )
    }
    assert columns["analysed_at"] == "timestamp with time zone"
    assert columns["analysed_verdict"] == "text" and columns["analysed_count"] == "integer"
