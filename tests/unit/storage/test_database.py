import asyncpg
import pytest

from legion.storage.database import DatabasePool


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
