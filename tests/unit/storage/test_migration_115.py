"""Migration 115 — local models: the run cap, the watcher's local-first model, and the slot leases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_115_present() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(115) == 1, "migration 115 (local models) must exist exactly once"
    sql = next(s for v, _, s in MIGRATIONS if v == 115)
    assert "local_max_runs" in sql and "watcher_local_model" in sql and "local_model_leases" in sql


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_no_local_first_by_default_and_the_run_cap_left_for_the_settings(
    db_with_migrations: DatabasePool,
) -> None:
    """The run cap this migration added left for the settings document's local_models.max_runs with migration 161
    (tests/unit/storage/test_migration_161.py)."""
    from embry0.storage.repositories.provider_config import ProviderConfigRepository

    db = db_with_migrations
    await db.execute("INSERT INTO provider_config (id) VALUES ('default') ON CONFLICT (id) DO NOTHING")
    assert await db.fetchval("SELECT watcher_local_model FROM provider_config WHERE id = 'default'") == ""
    row = await ProviderConfigRepository(db).get()
    # #164: the column stays, for a rollback, and is no longer part of the provider settings.
    assert "watcher_local_model" not in row
    assert "local_max_runs" not in row, "the provider settings still answer the run cap migration 161 moved"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_leases_hold_the_cap_free_on_release_and_expire_by_themselves(db_with_migrations: DatabasePool) -> None:
    from embry0.storage.repositories.local_model_leases import LocalModelLeasesRepository

    leases = LocalModelLeasesRepository(db_with_migrations)
    now = datetime.now(UTC)
    args = {"server": "cred-a", "job_id": "job-1", "agent_type": "analysis", "model": "custom/qwen"}
    first = await leases.try_take(cap=1, run_seconds=600, now=now, **args)
    assert first is not None
    assert await leases.try_take(cap=1, run_seconds=600, now=now, **args) is None, "the one slot is held"
    assert await leases.try_take(cap=2, run_seconds=600, now=now, **args) is not None, "a cap of two has room"
    await leases.release(first)
    assert len(await leases.live(now)) == 1

    # A lease left by a process that died frees its slot once the run's time (plus a margin) has passed.
    later = now + timedelta(seconds=600 + 6 * 60)
    assert await leases.live(later) == []
    assert await leases.try_take(cap=1, run_seconds=60, now=later, **args) is not None
