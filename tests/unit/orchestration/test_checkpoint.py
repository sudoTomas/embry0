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
