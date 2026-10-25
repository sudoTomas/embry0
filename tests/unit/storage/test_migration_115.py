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


def test_migration_115_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_114.py)."""
    assert MIGRATIONS[-1][0] == 115, "migration 115 must add the local model settings and leases"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_one_run_at_a_time_and_no_local_first_by_default(db_with_migrations: DatabasePool) -> None:
    from embry0.storage.repositories.provider_config import ProviderConfigRepository

    db = db_with_migrations
    await db.execute("INSERT INTO provider_config (id) VALUES ('default') ON CONFLICT (id) DO NOTHING")
    row = await ProviderConfigRepository(db).get()
    assert (row["local_max_runs"], row["watcher_local_model"]) == (1, "")
    updated = await ProviderConfigRepository(db).update(local_max_runs=2, watcher_local_model="custom/qwen")
    assert (updated["local_max_runs"], updated["watcher_local_model"]) == (2, "custom/qwen")
    with pytest.raises(Exception, match="local_max_runs"):
        await db.execute("UPDATE provider_config SET local_max_runs = 0 WHERE id = 'default'")


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_leases_hold_the_cap_free_on_release_and_expire_by_themselves(db_with_migrations: DatabasePool) -> None:
    from embry0.storage.repositories.local_model_leases import LocalModelLeasesRepository

    leases = LocalModelLeasesRepository(db_with_migrations)
    now = datetime.now(UTC)
    args = {"job_id": "job-1", "agent_type": "analysis", "model": "custom/qwen"}
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
