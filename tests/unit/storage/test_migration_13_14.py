"""Migrations #13 (drop untouched config seed rows) and #14 (bump dev agent model)."""

import os

import pytest

from embry0.storage.database import DatabasePool

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def db_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", "postgresql://embry0:embry0@localhost:5432/embry0_test")


@pytest.fixture
async def fresh_db(db_url: str) -> DatabasePool:
    """A connected pool with a wiped schema. Each test reapplies migrations
    up to the version it cares about. Skips when Postgres is unavailable."""
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


# --- Migration #13 ---


@pytest.mark.asyncio
async def test_migration_13_deletes_untouched_provider_seed(fresh_db: DatabasePool):
    """A provider_config row that exactly matches the original seed must be deleted."""
    await _apply_migrations_up_to(fresh_db, 12)
    # Migration #2 has seeded the row. Confirm it's there.
    row = await fresh_db.fetchrow("SELECT * FROM provider_config WHERE id = 'default'")
    assert row is not None

    # Apply migration #13.
    await _apply_migrations_up_to(fresh_db, 13)
    row = await fresh_db.fetchrow("SELECT * FROM provider_config WHERE id = 'default'")
    assert row is None


@pytest.mark.asyncio
async def test_migration_13_keeps_touched_provider_row(fresh_db: DatabasePool):
    """A provider_config row that the user has customized must NOT be deleted."""
    await _apply_migrations_up_to(fresh_db, 12)
    # Simulate the user saving a non-default value via the UI before #13 runs.
    await fresh_db.execute("UPDATE provider_config SET provider_mode = 'claude_max' WHERE id = 'default'")

    await _apply_migrations_up_to(fresh_db, 13)
    row = await fresh_db.fetchrow("SELECT provider_mode FROM provider_config WHERE id = 'default'")
    assert row is not None
    assert row["provider_mode"] == "claude_max"


@pytest.mark.asyncio
async def test_migration_13_deletes_untouched_integration_seed(fresh_db: DatabasePool):
    await _apply_migrations_up_to(fresh_db, 12)
    row = await fresh_db.fetchrow("SELECT * FROM integration_config WHERE id = 'default'")
    assert row is not None

    await _apply_migrations_up_to(fresh_db, 13)
    row = await fresh_db.fetchrow("SELECT * FROM integration_config WHERE id = 'default'")
    assert row is None


@pytest.mark.asyncio
async def test_migration_13_keeps_touched_integration_row(fresh_db: DatabasePool):
    await _apply_migrations_up_to(fresh_db, 12)
    # Pass the Python list directly — the asyncpg JSONB codec encodes it.
    await fresh_db.execute(
        "UPDATE integration_config SET trigger_labels = $1::jsonb WHERE id = 'default'",
        ["Custom"],
    )

    await _apply_migrations_up_to(fresh_db, 13)
    row = await fresh_db.fetchrow("SELECT trigger_labels FROM integration_config WHERE id = 'default'")
    assert row is not None
    # asyncpg's JSONB decoder returns the Python list directly.
    assert row["trigger_labels"] == ["Custom"]


# --- Migration #14 ---


@pytest.mark.asyncio
async def test_migration_14_bumps_default_developer_model(fresh_db: DatabasePool):
    """If the developer agent_definitions row is still at the seed value, bump it."""
    await _apply_migrations_up_to(fresh_db, 13)
    row = await fresh_db.fetchrow("SELECT model FROM agent_definitions WHERE type = 'developer' AND is_builtin = true")
    assert row is not None
    assert row["model"] == "claude-opus-4-6"

    await _apply_migrations_up_to(fresh_db, 14)
    row = await fresh_db.fetchrow("SELECT model FROM agent_definitions WHERE type = 'developer' AND is_builtin = true")
    assert row is not None
    assert row["model"] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_migration_14_does_not_clobber_customized_model(fresh_db: DatabasePool):
    """If the user has changed the model from the seed, leave it alone."""
    await _apply_migrations_up_to(fresh_db, 13)
    await fresh_db.execute(
        "UPDATE agent_definitions SET model = 'claude-haiku-4-5' WHERE type = 'developer' AND is_builtin = true"
    )

    await _apply_migrations_up_to(fresh_db, 14)
    row = await fresh_db.fetchrow("SELECT model FROM agent_definitions WHERE type = 'developer' AND is_builtin = true")
    assert row is not None
    assert row["model"] == "claude-haiku-4-5"
