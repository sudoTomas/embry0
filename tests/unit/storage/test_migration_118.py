"""Migration 118 — slop is not a bug: the slopometer's verdict on a job and on a watcher finding."""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_118_present() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(118) == 1, "migration 118 (slop dungeon) must exist exactly once"
    sql = next(s for v, _, s in MIGRATIONS if v == 118)
    assert "jobs ADD COLUMN IF NOT EXISTS work_nature JSONB" in sql
    assert "healing_cases ADD COLUMN IF NOT EXISTS work_nature JSONB" in sql


def test_migration_118_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_117.py)."""
    assert MIGRATIONS[-1][0] == 118, "migration 118 must add work_nature"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_job_keeps_its_verdict(db_with_migrations: DatabasePool) -> None:
    from embry0.storage.repositories.jobs import JobsRepository

    jobs = JobsRepository(db_with_migrations)
    job_id = await jobs.create(repo="acme/app", task="t")
    assert (await jobs.get(job_id) or {}).get("work_nature") is None, "a job starts unclassified"
    verdict = {"nature": "slop", "slop_score": 72, "kinds": ["symptom_patch"], "evidence": []}
    await jobs.update(job_id, work_nature=verdict)
    stored = await jobs.get(job_id)
    assert stored is not None and stored["work_nature"]["nature"] == "slop"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_watcher_case_keeps_its_verdict(db_with_migrations: DatabasePool) -> None:
    from embry0.services.healing.service import HealingCaseService
    from embry0.storage.repositories.healing_cases import HealingCasesRepository

    service = HealingCaseService(HealingCasesRepository(db_with_migrations))
    verdict = {"nature": "slop", "slop_score": 60, "kinds": ["duplication"], "evidence": []}
    slop = await service.open_case(repo="acme/app", origin="watcher", title="Clean up", work_nature=verdict)
    plain = await service.open_case(repo="acme/app", origin="human", title="Fix it")
    assert slop["work_nature"]["kinds"] == ["duplication"]
    assert plain["work_nature"] is None, "a case opened without a verdict stays unclassified"
