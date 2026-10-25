"""Migration 113 — provider fallback: the fallback order and each run's substitution."""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_113_present() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(113) == 1, "migration 113 (provider fallback) must exist exactly once"
    sql = next(s for v, _, s in MIGRATIONS if v == 113)
    assert "provider_config" in sql and "fallback_models" in sql
    assert "traces" in sql and "fallback" in sql


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_order_defaults_to_off_and_a_trace_records_a_substitution(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await db.execute("INSERT INTO provider_config (id) VALUES ('default') ON CONFLICT (id) DO NOTHING")
    assert await db.fetchval("SELECT fallback_models FROM provider_config WHERE id = 'default'") == []
    await db.execute("INSERT INTO jobs (job_id, repo, task, status) VALUES ('job-1', 'acme/widgets', 't', 'running')")
    await db.execute(
        "INSERT INTO traces (trace_id, job_id, agent_type, model, result, fallback)"
        " VALUES ('run-1', 'job-1', 'developer', 'grok-4.7', 'success', $1)",
        {"instead_of": "claude-opus-5", "provider": "anthropic", "reason": "quota_exhausted", "until": None},
    )
    assert (await db.fetchval("SELECT fallback FROM traces WHERE trace_id = 'run-1'"))["instead_of"] == "claude-opus-5"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_traces_repository_writes_the_substitution(db_with_migrations: DatabasePool) -> None:
    from embry0.storage.repositories.traces import TracesRepository

    db = db_with_migrations
    await db.execute("INSERT INTO jobs (job_id, repo, task, status) VALUES ('job-2', 'acme/widgets', 't', 'running')")
    traces = TracesRepository(db)
    record = {"instead_of": "claude-sonnet-5", "provider": "anthropic", "reason": "missing", "until": None}
    await traces.create(
        job_id="job-2", agent_type="review", model="grok-4.7", result="running", trace_id="run-a", fallback=record
    )
    await traces.create(
        job_id="job-2", agent_type="review", model="claude-sonnet-5", result="running", trace_id="run-b"
    )
    assert (await traces.get("run-a"))["fallback"] == record
    assert (await traces.get("run-b"))["fallback"] is None
