"""Migration #15 — FK ON DELETE CASCADE / SET NULL policies.

Tests verify that after migration 15 is applied:
  - traces.job_id has ON DELETE CASCADE
  - job_logs.job_id has ON DELETE CASCADE
  - issue_inputs.issue_id has ON DELETE CASCADE
  - issue_inputs.job_id has ON DELETE CASCADE
  - jobs.issue_id has ON DELETE SET NULL
  - issues.parent_issue_id has ON DELETE SET NULL

Tests skip cleanly if PostgreSQL is unavailable.
"""

import os

import pytest

from embry0.storage.database import DatabasePool

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def db_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", "postgresql://embry0:embry0@localhost:5432/embry0_test")


@pytest.fixture
async def fresh_db(db_url: str) -> DatabasePool:
    """A connected pool with a wiped schema. Skips when Postgres is unavailable."""
    import asyncpg

    db = DatabasePool(db_url)
    try:
        await db.connect()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    async with db.pool.acquire() as conn:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    yield db
    await db.close()


async def _apply_migrations_up_to(db: DatabasePool, last_version: int) -> None:
    """Apply un-applied migrations up to (and including) ``last_version``."""
    from embry0.storage.migrations.runner import MIGRATIONS

    async with db.pool.acquire() as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS embry0_migrations ("
            "version INTEGER PRIMARY KEY, "
            "description TEXT NOT NULL, "
            "applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
        )
        current = await conn.fetchval("SELECT COALESCE(MAX(version), 0) FROM embry0_migrations")
        for version, description, sql in MIGRATIONS:
            if version <= current:
                continue
            if version > last_version:
                break
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO embry0_migrations (version, description) VALUES ($1, $2)",
                    version,
                    description,
                )


async def _get_fk_delete_rule(db: DatabasePool, table_name: str, column_name: str) -> str | None:
    """Return the ON DELETE rule for the FK on table.column, or None if no FK found."""
    row = await db.fetchrow(
        """
        SELECT rc.delete_rule
        FROM information_schema.referential_constraints rc
        JOIN information_schema.key_column_usage kcu
            ON kcu.constraint_name = rc.constraint_name
           AND kcu.constraint_schema = rc.constraint_schema
        WHERE kcu.table_name = $1
          AND kcu.column_name = $2
          AND kcu.table_schema = 'public'
        """,
        table_name,
        column_name,
    )
    return row["delete_rule"] if row else None


# ---- Migration 15 tests ----


@pytest.mark.asyncio
async def test_migration_15_traces_job_id_cascade(fresh_db: DatabasePool) -> None:
    """traces.job_id FK must have ON DELETE CASCADE after migration 15."""
    await _apply_migrations_up_to(fresh_db, 15)
    rule = await _get_fk_delete_rule(fresh_db, "traces", "job_id")
    assert rule == "CASCADE", f"Expected CASCADE, got {rule!r}"


@pytest.mark.asyncio
async def test_migration_15_job_logs_job_id_cascade(fresh_db: DatabasePool) -> None:
    """job_logs.job_id FK must have ON DELETE CASCADE after migration 15."""
    await _apply_migrations_up_to(fresh_db, 15)
    rule = await _get_fk_delete_rule(fresh_db, "job_logs", "job_id")
    assert rule == "CASCADE", f"Expected CASCADE, got {rule!r}"


@pytest.mark.asyncio
async def test_migration_15_issue_inputs_issue_id_cascade(fresh_db: DatabasePool) -> None:
    """issue_inputs.issue_id FK must have ON DELETE CASCADE after migration 15."""
    await _apply_migrations_up_to(fresh_db, 15)
    rule = await _get_fk_delete_rule(fresh_db, "issue_inputs", "issue_id")
    assert rule == "CASCADE", f"Expected CASCADE, got {rule!r}"


@pytest.mark.asyncio
async def test_migration_15_issue_inputs_job_id_cascade(fresh_db: DatabasePool) -> None:
    """issue_inputs.job_id FK must have ON DELETE CASCADE after migration 15."""
    await _apply_migrations_up_to(fresh_db, 15)
    rule = await _get_fk_delete_rule(fresh_db, "issue_inputs", "job_id")
    assert rule == "CASCADE", f"Expected CASCADE, got {rule!r}"


@pytest.mark.asyncio
async def test_migration_15_jobs_issue_id_set_null(fresh_db: DatabasePool) -> None:
    """jobs.issue_id FK must have ON DELETE SET NULL after migration 15."""
    await _apply_migrations_up_to(fresh_db, 15)
    rule = await _get_fk_delete_rule(fresh_db, "jobs", "issue_id")
    assert rule == "SET NULL", f"Expected SET NULL, got {rule!r}"


@pytest.mark.asyncio
async def test_migration_15_issues_parent_issue_id_set_null(fresh_db: DatabasePool) -> None:
    """issues.parent_issue_id FK must have ON DELETE SET NULL after migration 15."""
    await _apply_migrations_up_to(fresh_db, 15)
    rule = await _get_fk_delete_rule(fresh_db, "issues", "parent_issue_id")
    assert rule == "SET NULL", f"Expected SET NULL, got {rule!r}"


@pytest.mark.asyncio
async def test_migration_15_cascade_deletes_traces_with_job(fresh_db: DatabasePool) -> None:
    """Deleting a job must cascade-delete its traces."""
    await _apply_migrations_up_to(fresh_db, 15)

    await fresh_db.execute("INSERT INTO jobs (job_id, repo, task) VALUES ('job-m15', 'owner/repo', 'test task')")
    await fresh_db.execute(
        """
        INSERT INTO traces (trace_id, job_id, agent_type, model, result)
        VALUES ('trace-m15', 'job-m15', 'developer', 'claude-opus-4-7', 'ok')
        """
    )

    # Confirm trace exists
    count = await fresh_db.fetchval("SELECT COUNT(*) FROM traces WHERE job_id = 'job-m15'")
    assert count == 1

    # Delete the job — traces must cascade
    await fresh_db.execute("DELETE FROM jobs WHERE job_id = 'job-m15'")

    count = await fresh_db.fetchval("SELECT COUNT(*) FROM traces WHERE job_id = 'job-m15'")
    assert count == 0, "Traces should have been cascade-deleted with the job"


@pytest.mark.asyncio
async def test_migration_15_set_null_jobs_on_issue_delete(fresh_db: DatabasePool) -> None:
    """Deleting an issue must SET NULL on jobs.issue_id (not cascade-delete the job)."""
    await _apply_migrations_up_to(fresh_db, 15)

    await fresh_db.execute("INSERT INTO issues (id, title) VALUES ('issue-m15', 'Test Issue')")
    await fresh_db.execute(
        "INSERT INTO jobs (job_id, repo, task, issue_id) VALUES ('job-m15b', 'owner/repo', 'test task', 'issue-m15')"
    )

    # Confirm job references the issue
    row = await fresh_db.fetchrow("SELECT issue_id FROM jobs WHERE job_id = 'job-m15b'")
    assert row["issue_id"] == "issue-m15"

    # Delete the issue — job must survive with issue_id = NULL
    await fresh_db.execute("DELETE FROM issues WHERE id = 'issue-m15'")

    row = await fresh_db.fetchrow("SELECT issue_id FROM jobs WHERE job_id = 'job-m15b'")
    assert row is not None, "Job should still exist after issue deletion"
    assert row["issue_id"] is None, "jobs.issue_id should be NULL after issue deletion"


@pytest.mark.asyncio
async def test_migration_15_is_idempotent(fresh_db: DatabasePool) -> None:
    """Applying migration 15 a second time must not raise (IF NOT EXISTS / IF EXISTS guards)."""
    await _apply_migrations_up_to(fresh_db, 15)
    # Re-running the migration 15 SQL directly should be a no-op due to DROP IF EXISTS guards.
    from embry0.storage.migrations.runner import MIGRATIONS

    sql_15 = next(sql for ver, _desc, sql in MIGRATIONS if ver == 15)
    async with fresh_db.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(sql_15)  # Must not raise
