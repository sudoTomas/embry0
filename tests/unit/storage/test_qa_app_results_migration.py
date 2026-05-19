"""Integration test: qa_app_results migration applies cleanly + has the right shape.

Uses the existing `db_with_migrations` fixture from tests/conftest.py which
provisions a clean test DB with all migrations applied via run_migrations.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.requires_postgres


@pytest.mark.asyncio
async def test_qa_app_results_table_exists(db_with_migrations):
    oid = await db_with_migrations.fetchval("SELECT to_regclass('qa_app_results')")
    assert oid is not None, "qa_app_results table was not created"


@pytest.mark.asyncio
async def test_qa_app_results_has_expected_columns(db_with_migrations):
    expected = {
        "id",
        "job_id",
        "app_name",
        "status",
        "started_at",
        "duration_ms",
        "cache_hits",
        "trace_url",
        "failure_summary",
        "raw_result",
        "created_at",
        "updated_at",
    }
    rows = await db_with_migrations.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'qa_app_results'
        """
    )
    actual = {r["column_name"] for r in rows}
    assert expected <= actual, f"missing columns: {expected - actual}"


@pytest.mark.asyncio
async def test_qa_app_results_unique_constraint_on_job_and_app(db_with_migrations):
    """Composite uniqueness on (job_id, app_name) — required for sub-task
    retries to upsert without duplicating rows."""
    rows = await db_with_migrations.fetch(
        """
        SELECT indexdef FROM pg_indexes
        WHERE tablename = 'qa_app_results'
          AND indexname = 'uq_qa_app_results_job_app'
        """
    )
    assert len(rows) == 1
    assert "UNIQUE" in rows[0]["indexdef"].upper()
    assert "job_id" in rows[0]["indexdef"]
    assert "app_name" in rows[0]["indexdef"]


@pytest.mark.asyncio
async def test_qa_app_results_status_check_constraint(db_with_migrations):
    """Status must be one of the seven valid SubTaskStatus values."""
    # Insert a parent jobs row first to satisfy FK
    await db_with_migrations.execute(
        "INSERT INTO jobs (job_id, repo, task) VALUES ('test-job-1', 'org/repo', 'test')"
        " ON CONFLICT (job_id) DO NOTHING"
    )

    # Valid status — should succeed
    await db_with_migrations.execute(
        """
        INSERT INTO qa_app_results (job_id, app_name, status)
        VALUES ('test-job-1', 'hub', 'passed')
        """
    )

    # Invalid status — should fail
    with pytest.raises(Exception) as exc:
        await db_with_migrations.execute(
            """
            INSERT INTO qa_app_results (job_id, app_name, status)
            VALUES ('test-job-1', 'companion', 'unknown_state')
            """
        )
    assert "check" in str(exc.value).lower() or "constraint" in str(exc.value).lower()
