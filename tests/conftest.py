"""Shared test fixtures."""

from collections.abc import AsyncIterator

import asyncpg
import pytest


@pytest.fixture
async def pg_pool() -> AsyncIterator[asyncpg.Pool]:
    """Create a temporary test database and return a connection pool.

    Requires a running PostgreSQL instance at localhost:5432.
    Uses DATABASE_URL env var or defaults to test database.
    Falls back to skipping if PostgreSQL is unavailable.
    """
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test")

    try:
        # Create database if needed
        sys_url = url.rsplit("/", 1)[0] + "/postgres"
        sys_conn = await asyncpg.connect(sys_url)
        db_name = url.rsplit("/", 1)[1]
        try:
            await sys_conn.execute(f"CREATE DATABASE {db_name}")
        except asyncpg.DuplicateDatabaseError:
            pass
        await sys_conn.close()

        pool = await asyncpg.create_pool(url, min_size=1, max_size=5)
        assert pool is not None

        yield pool

        # Cleanup tables after test
        async with pool.acquire() as conn:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
        await pool.close()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")
