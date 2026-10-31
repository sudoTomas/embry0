"""Migration 119 — job briefs: each job in plain words."""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_119_present() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(119) == 1, "migration 119 (job briefs) must exist exactly once"
    sql = next(s for v, _, s in MIGRATIONS if v == 119)
    assert "job_briefs" in sql and "input_hash" in sql


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_briefs_are_written_live_jobs_first_and_shown_while_they_match(db_with_migrations: DatabasePool) -> None:
    from embry0.services.job_briefs import JobBrief, input_hash
    from embry0.storage.repositories.job_briefs import JobBriefsRepository
    from embry0.storage.repositories.jobs import JobsRepository

    jobs = JobsRepository(db_with_migrations)
    briefs = JobBriefsRepository(db_with_migrations)
    done = await jobs.create(repo="acme/app", task="Fix the login page")
    await jobs.update(done, status="running")
    await jobs.update(done, status="completed")
    live = await jobs.create(repo="acme/app", task="[ENG-12] Find why the worker crash-loops")
    await jobs.update(live, status="running", area="Billing", job_kind="code")

    candidates = await briefs.candidates()
    assert [c["job_id"] for c in candidates][:2] == [live, done], "live jobs first"
    assert all(c["brief_hash"] is None for c in candidates)
    row = next(c for c in candidates if c["job_id"] == live)
    assert row["area"] == "Billing" and row["task"].startswith("[ENG-12]")

    brief = JobBrief(
        "Find why the worker crash-loops", "It restarts.", "Work stops.", "It stays up. You get a PR.", "medium"
    )
    await briefs.save(live, input_hash(row), brief, model="m", prompt_hash="p1")
    current = await briefs.current([live, done])
    assert set(current) == {live} and current[live]["title"] == brief.title
    assert next(c for c in await briefs.candidates() if c["job_id"] == live)["brief_hash"] == input_hash(row)

    await jobs.update(live, work_nature={"nature": "slop", "slop_score": 70})
    assert await briefs.current([live]) == {}, "new inputs hide the old brief until it is rewritten"
