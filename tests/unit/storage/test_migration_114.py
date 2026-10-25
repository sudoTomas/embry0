"""Migration 114 — provider_catalogs: OpenRouter's live model list, and the last good one kept through a failed read."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_114_present() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(114) == 1, "migration 114 (provider catalogs) must exist exactly once"
    sql = next(s for v, _, s in MIGRATIONS if v == 114)
    assert "provider_catalogs" in sql and "last_error" in sql


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_failed_read_keeps_the_stored_list(db_with_migrations: DatabasePool) -> None:
    from embry0.storage.repositories.provider_catalogs import ProviderCatalogsRepository

    repo = ProviderCatalogsRepository(db_with_migrations)
    assert await repo.get("openrouter") is None

    read_at = datetime(2026, 10, 25, 12, tzinfo=UTC)
    model = {"model": "openrouter/anthropic/claude-opus-5.5", "input_usd_per_mtok": 4.0, "output_usd_per_mtok": 20.0}
    await repo.save("openrouter", [model], {"batch": 74}, read_at)
    failed_at = datetime(2026, 10, 26, 12, tzinfo=UTC)
    await repo.save_error("openrouter", "OpenRouter answered 503 for its model list", failed_at)

    stored = await repo.get("openrouter")
    assert stored is not None
    assert stored["models"] == [model] and stored["skipped"] == {"batch": 74}
    assert stored["fetched_at"] == read_at
    assert (stored["last_error"], stored["last_error_at"]) == ("OpenRouter answered 503 for its model list", failed_at)

    await repo.save("openrouter", [model], {}, failed_at)
    stored = await repo.get("openrouter")
    assert stored is not None and stored["last_error"] is None, "a good read clears the error"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_first_read_that_fails_leaves_an_empty_list(db_with_migrations: DatabasePool) -> None:
    from embry0.storage.repositories.provider_catalogs import ProviderCatalogsRepository

    repo = ProviderCatalogsRepository(db_with_migrations)
    await repo.save_error("openrouter", "could not reach OpenRouter's model list: ConnectError", datetime.now(UTC))
    stored = await repo.get("openrouter")
    assert stored is not None and stored["models"] == [] and stored["fetched_at"] is None
