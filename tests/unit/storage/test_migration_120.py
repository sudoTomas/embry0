"""Migration 120 — done when: the finish line a code job works to."""

from __future__ import annotations

import json

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_120_present() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(120) == 1, "migration 120 (done when) must exist exactly once"
    sql = next(s for v, _, s in MIGRATIONS if v == 120)
    assert "done_when" in sql and "jobs" in sql


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_job_keeps_its_list_and_null_means_none(db_with_migrations: DatabasePool) -> None:
    from embry0.storage.repositories.jobs import JobsRepository

    jobs = JobsRepository(db_with_migrations)
    job_id = await jobs.create(repo="acme/app", task="Fix the export")
    assert (await jobs.get(job_id))["done_when"] is None
    listed = {
        "items": [{"id": "d1", "done": "CSV has a header", "shown_by": "a test"}],
        "out_of_scope": [],
        "source": "triage",
    }
    await jobs.update(job_id, done_when=listed)
    stored = (await jobs.get(job_id))["done_when"]
    stored = stored if isinstance(stored, dict) else json.loads(stored)
    assert stored["items"][0]["done"] == "CSV has a header"
