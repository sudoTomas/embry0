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
