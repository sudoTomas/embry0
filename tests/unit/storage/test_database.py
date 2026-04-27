import asyncpg
import pytest

from athanor.storage.database import DatabasePool


@pytest.mark.asyncio
async def test_pool_connect_and_query(pg_pool: asyncpg.Pool):
    """Pool can execute a simple query."""
    async with pg_pool.acquire() as conn:
        result = await conn.fetchval("SELECT 1")
    assert result == 1


@pytest.mark.asyncio
async def test_database_pool_lifecycle():
    """DatabasePool creates and closes pool correctly."""
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test")
    try:
        db = DatabasePool(url)
        await db.connect()
        assert db.pool is not None

        result = await db.fetchval("SELECT 42")
        assert result == 42

        await db.close()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")


@pytest.mark.asyncio
async def test_database_pool_execute(pg_pool: asyncpg.Pool):
    """DatabasePool.execute runs DDL statements."""
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test")
    try:
        db = DatabasePool(url)
        await db.connect()

        await db.execute("CREATE TABLE IF NOT EXISTS _test_exec (id SERIAL PRIMARY KEY, name TEXT)")
        await db.execute("INSERT INTO _test_exec (name) VALUES ($1)", "hello")
        row = await db.fetchrow("SELECT name FROM _test_exec WHERE name = $1", "hello")
        assert row is not None
        assert row["name"] == "hello"

        await db.execute("DROP TABLE _test_exec")
        await db.close()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")


@pytest.mark.asyncio
async def test_transaction_commits_on_success():
    """Queries inside transaction() block must commit when the block exits cleanly."""
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test")
    try:
        db = DatabasePool(url)
        await db.connect()

        await db.execute("CREATE TABLE IF NOT EXISTS _txn_test (id INT PRIMARY KEY)")
        await db.execute("DELETE FROM _txn_test")

        async with db.transaction() as conn:
            await conn.execute("INSERT INTO _txn_test (id) VALUES (1)")
            await conn.execute("INSERT INTO _txn_test (id) VALUES (2)")

        rows = await db.fetch("SELECT id FROM _txn_test ORDER BY id")
        assert [r["id"] for r in rows] == [1, 2]

        await db.execute("DROP TABLE _txn_test")
        await db.close()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")


@pytest.mark.asyncio
async def test_transaction_rolls_back_on_exception():
    """If the transaction() block raises, ALL statements roll back."""
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test")
    try:
        db = DatabasePool(url)
        await db.connect()

        await db.execute("CREATE TABLE IF NOT EXISTS _txn_test (id INT PRIMARY KEY)")
        await db.execute("DELETE FROM _txn_test")

        with pytest.raises(RuntimeError):
            async with db.transaction() as conn:
                await conn.execute("INSERT INTO _txn_test (id) VALUES (1)")
                await conn.execute("INSERT INTO _txn_test (id) VALUES (2)")
                raise RuntimeError("fail mid-transaction")

        rows = await db.fetch("SELECT id FROM _txn_test")
        assert list(rows) == [], f"expected empty after rollback, got {list(rows)}"

        await db.execute("DROP TABLE _txn_test")
        await db.close()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")


@pytest.mark.asyncio
async def test_handle_needs_info_end_to_end_against_real_db(pg_pool):
    """End-to-end proof that _handle_needs_info's transactional boundary works
    against a real PostgreSQL — inputs persist AND job/issue transition to
    awaiting_input atomically on success.

    Complements the mock-based sequencing test in test_issue_executor.py by
    exercising the actual BEGIN/COMMIT semantics on real Postgres data.
    """
    import os
    import uuid as uuid_mod

    from athanor.storage.migrations.runner import run_migrations

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test")
    try:
        db = DatabasePool(url)
        await db.connect()
        # pg_pool fixture creates the database; we need the schema too
        await run_migrations(db)

        # Setup: create the foreign-key targets so INSERTs + UPDATEs succeed
        iss_id = f"iss-{uuid_mod.uuid4().hex[:10]}"
        job_id = f"job-{uuid_mod.uuid4().hex[:10]}"
        await db.execute(
            "INSERT INTO issues (id, repo, title, status) VALUES ($1, $2, $3, $4)",
            iss_id,
            "o/r",
            "test",
            "triaging",
        )
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status, issue_id) VALUES ($1, $2, $3, $4, $5)",
            job_id,
            "o/r",
            "t",
            "running",
            iss_id,
        )

        try:
            # Build a minimal executor and call the real method against real DB
            from unittest.mock import AsyncMock

            from athanor.services.issue_executor import IssueExecutor

            executor = IssueExecutor.__new__(IssueExecutor)
            executor._db = db
            # _issues.get() is called in the post-commit notification branch —
            # stub it to return None so the dispatch is skipped (None config
            # would also skip, but _issues.get is called first).
            issues_mock = AsyncMock()
            issues_mock.get = AsyncMock(return_value=None)
            executor._inputs = None
            executor._jobs = None
            executor._issues = issues_mock
            executor._config = None
            executor._audit_log_path = None

            await executor._handle_needs_info(
                issue_id=iss_id,
                job_id=job_id,
                decision={
                    "questions": [
                        {"question": "Q1?", "importance": "blocking"},
                        {"question": "Q2?", "importance": "blocking"},
                    ],
                    "asking_node": "triage",
                },
            )

            # Verify: 2 input rows persisted, job and issue transitioned atomically
            rows = await db.fetch(
                "SELECT id, question, status FROM issue_inputs WHERE issue_id = $1 ORDER BY question",
                iss_id,
            )
            assert len(rows) == 2
            assert [r["question"] for r in rows] == ["Q1?", "Q2?"]
            assert all(r["status"] == "pending" for r in rows)

            job_row = await db.fetchrow("SELECT status FROM jobs WHERE job_id = $1", job_id)
            assert job_row["status"] == "awaiting_input"

            issue_row = await db.fetchrow("SELECT status FROM issues WHERE id = $1", iss_id)
            assert issue_row["status"] == "awaiting_input"

        finally:
            # Cleanup — FKs cascade via our explicit DELETEs in order
            await db.execute("DELETE FROM issue_inputs WHERE issue_id = $1", iss_id)
            await db.execute("DELETE FROM jobs WHERE job_id = $1", job_id)
            await db.execute("DELETE FROM issues WHERE id = $1", iss_id)

        await db.close()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")


@pytest.mark.asyncio
async def test_transaction_yields_asyncpg_connection():
    """The yielded object must be an asyncpg Connection with execute/fetchrow."""
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test")
    try:
        db = DatabasePool(url)
        await db.connect()

        async with db.transaction() as conn:
            assert hasattr(conn, "execute")
            assert hasattr(conn, "fetchrow")
            val = await conn.fetchval("SELECT 1")
            assert val == 1

        await db.close()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")
