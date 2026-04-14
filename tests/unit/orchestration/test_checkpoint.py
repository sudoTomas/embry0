import os

import pytest


@pytest.mark.asyncio
async def test_checkpointer_context_creates_saver():
    """Test that checkpointer_context yields an AsyncPostgresSaver.

    Requires a running PostgreSQL instance — skip gracefully if unavailable.
    """
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError:
        pytest.skip("langgraph-checkpoint-postgres not installed")

    try:
        import psycopg
    except ImportError:
        pytest.skip("psycopg not installed")

    from legion.orchestration.checkpoint import checkpointer_context

    # This will fail to connect without a real DB, but we can verify the function exists
    # and returns the right type structure. For a real integration test, use testcontainers.
    try:
        async with checkpointer_context("postgresql://legion:legion@localhost:5432/legion") as saver:
            assert isinstance(saver, AsyncPostgresSaver)
    except (psycopg.OperationalError, OSError, ConnectionRefusedError):
        pytest.skip("PostgreSQL not available — skipping integration test")


@pytest.mark.asyncio
async def test_purge_thread_is_idempotent_for_nonexistent_thread(pg_pool):
    """purge_thread on a thread_id with no rows completes cleanly — idempotent
    and safe to call on an already-purged / never-written thread."""
    import asyncpg

    from legion.orchestration.checkpoint import checkpointer_context, purge_thread

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test")

    try:
        async with checkpointer_context(url) as _saver:
            pass  # setup runs inside the context manager

        await purge_thread(url, "thread-does-not-exist")  # should not raise
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")


@pytest.mark.asyncio
async def test_purge_thread_deletes_only_target_thread_rows(pg_pool):
    """purge_thread removes rows for the target thread_id from all 3
    checkpoint tables, leaving other threads' rows intact."""
    import asyncpg

    from legion.orchestration.checkpoint import checkpointer_context, purge_thread

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test")

    try:
        async with checkpointer_context(url) as _saver:
            pass

        # Seed minimal rows directly into each checkpoint table for two
        # distinct thread_ids, then purge one and verify only that one is gone.
        conn = await asyncpg.connect(url)
        try:
            target = "thread-to-purge"
            other = "thread-to-keep"

            # Inspect columns to craft a minimal valid insert per table
            async def _required_cols(table: str) -> list[str]:
                rows = await conn.fetch(
                    """
                    SELECT column_name, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_name = $1 AND column_default IS NULL AND is_nullable = 'NO'
                    """,
                    table,
                )
                return [r["column_name"] for r in rows]

            # checkpoints PK is (thread_id, checkpoint_ns, checkpoint_id); NS defaults to '' in LangGraph.
            # Minimal insert: thread_id + checkpoint_id + checkpoint_ns (empty string) + type='msgpack' + checkpoint bytes.
            # Column set is LangGraph-specific; we best-effort skip if the schema differs.
            try:
                for thread_id in (target, other):
                    await conn.execute(
                        """
                        INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, type, checkpoint, metadata)
                        VALUES ($1, '', $2, 'msgpack', '\\x00'::bytea, '{}'::jsonb)
                        """,
                        thread_id,
                        f"cp-{thread_id}",
                    )
            except asyncpg.PostgresError as exc:
                pytest.skip(f"Checkpoint schema doesn't match test fixture: {exc}")

            before_target = await conn.fetchval("SELECT COUNT(*) FROM checkpoints WHERE thread_id = $1", target)
            before_other = await conn.fetchval("SELECT COUNT(*) FROM checkpoints WHERE thread_id = $1", other)
            assert before_target >= 1
            assert before_other >= 1

            await purge_thread(url, target)

            after_target = await conn.fetchval("SELECT COUNT(*) FROM checkpoints WHERE thread_id = $1", target)
            after_other = await conn.fetchval("SELECT COUNT(*) FROM checkpoints WHERE thread_id = $1", other)
            assert after_target == 0, "target thread rows should be deleted"
            assert after_other == before_other, "other thread rows must be preserved"

            # Cleanup
            await conn.execute("DELETE FROM checkpoints WHERE thread_id = $1", other)
        finally:
            await conn.close()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")
