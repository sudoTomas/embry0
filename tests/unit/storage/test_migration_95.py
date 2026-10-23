"""Migration 95 — job_logs carries run_id, node and event_type; traces.result admits 'running'."""

import json

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS
from embry0.storage.repositories.jobs import JobsRepository


def _sql_for(version: int) -> str:
    match = [sql for v, _, sql in MIGRATIONS if v == version]
    assert len(match) == 1, f"migration {version} must exist exactly once"
    return match[0]


def test_migration_95_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert 95 in versions, "migration 95 (job_logs correlation columns, #36) is missing"
    assert versions == list(range(1, len(versions) + 1))


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_append_log_event_fills_the_correlation_columns(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await db.execute(_sql_for(95))  # a second run must be a no-op
    jobs = JobsRepository(db)
    job_id = await jobs.create(repo="a/b", task="t")
    seq = await jobs.append_log_event(
        job_id, {"type": "tool_call", "run_id": "run-1", "node": "developer", "tool_name": "Bash"}
    )
    row = await db.fetchrow("SELECT run_id, node, event_type, text FROM job_logs WHERE id = $1", seq)
    assert row["run_id"] == "run-1" and row["node"] == "developer" and row["event_type"] == "tool_call"
    assert json.loads(row["text"])["tool_name"] == "Bash"
    legacy = await jobs.append_log_event(job_id, {"type": "system", "message": "queued"})
    legacy_row = await db.fetchrow("SELECT run_id, node, event_type FROM job_logs WHERE id = $1", legacy)
    assert legacy_row["run_id"] is None and legacy_row["node"] is None and legacy_row["event_type"] == "system"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_append_log_event_coerces_non_string_correlation_values(db_with_migrations: DatabasePool) -> None:
    """A non-string run_id/node/type must not fail the INSERT (and so drop the
    event); the TEXT columns get None and the JSON keeps the raw value."""
    jobs = JobsRepository(db_with_migrations)
    job_id = await jobs.create(repo="a/b", task="t")
    seq = await jobs.append_log_event(job_id, {"type": "progress", "run_id": 7, "node": ["qa"], "message": "m"})
    row = await db_with_migrations.fetchrow("SELECT run_id, node, event_type, text FROM job_logs WHERE id = $1", seq)
    assert row["run_id"] is None and row["node"] is None and row["event_type"] == "progress"
    assert json.loads(row["text"])["run_id"] == 7


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_agent_id_column_is_gone_and_indexes_exist(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    columns = {
        r["column_name"]
        for r in await db.fetch("SELECT column_name FROM information_schema.columns WHERE table_name = 'job_logs'")
    }
    assert "agent_id" not in columns and {"run_id", "node", "event_type"} <= columns
    indexes = {r["indexname"] for r in await db.fetch("SELECT indexname FROM pg_indexes WHERE tablename = 'job_logs'")}
    assert {"idx_job_logs_job_event_type", "idx_job_logs_run_id"} <= indexes
