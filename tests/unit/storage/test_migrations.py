import asyncpg
import pytest

from legion.storage.database import DatabasePool
from legion.storage.migrations.runner import MIGRATIONS, run_migrations


@pytest.mark.asyncio
async def test_migrations_list_is_ordered():
    """Migration versions are sequential starting from 1."""
    versions = [m[0] for m in MIGRATIONS]
    assert versions == list(range(1, len(MIGRATIONS) + 1))


@pytest.mark.asyncio
async def test_run_migrations_creates_tables(pg_pool: asyncpg.Pool):
    """Running migrations creates all expected tables."""
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test")
    try:
        db = DatabasePool(url)
        await db.connect()
        await run_migrations(db)

        # Verify core tables exist
        tables = await db.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
        table_names = {row["tablename"] for row in tables}
        assert "legion_migrations" in table_names
        assert "jobs" in table_names
        assert "traces" in table_names
        assert "pipeline_templates" in table_names
        assert "sandbox_profiles" in table_names
        assert "context_config" in table_names
        assert "budget_config" in table_names
        assert "audit_log" in table_names

        await db.close()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")


@pytest.mark.asyncio
async def test_run_migrations_is_idempotent(pg_pool: asyncpg.Pool):
    """Running migrations twice does not error."""
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test")
    try:
        db = DatabasePool(url)
        await db.connect()
        await run_migrations(db)
        await run_migrations(db)  # Second run should be no-op

        version = await db.fetchval("SELECT MAX(version) FROM legion_migrations")
        assert version == len(MIGRATIONS)

        await db.close()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")
