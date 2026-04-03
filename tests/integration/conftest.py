"""Integration test fixtures — requires running PostgreSQL."""

import os
from collections.abc import AsyncIterator

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from legion.api.app import create_app
from legion.config import LegionConfig
from legion.storage.database import DatabasePool
from legion.storage.migrations.runner import run_migrations


@pytest.fixture(scope="session")
def database_url() -> str:
    return os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://legion:legion@localhost:5432/legion_integration_test",
    )


@pytest.fixture(scope="session")
async def setup_database(database_url: str) -> AsyncIterator[str]:
    """Create and migrate the test database."""
    try:
        sys_url = database_url.rsplit("/", 1)[0] + "/postgres"
        sys_conn = await asyncpg.connect(sys_url)
        db_name = database_url.rsplit("/", 1)[1]
        try:
            await sys_conn.execute(f"CREATE DATABASE {db_name}")
        except asyncpg.DuplicateDatabaseError:
            pass
        await sys_conn.close()

        db = DatabasePool(database_url)
        await db.connect()
        await run_migrations(db)
        await db.close()

        yield database_url

        sys_conn = await asyncpg.connect(sys_url)
        await sys_conn.execute(f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)")
        await sys_conn.close()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")


@pytest.fixture
async def app(setup_database: str) -> AsyncIterator[AsyncClient]:
    """Create a test FastAPI app with real database."""
    config = LegionConfig(
        _env_file=None,
        database_url=setup_database,
        dev_mode=True,
        api_key="",
        github_webhook_secret="test-secret",
    )
    fastapi_app = create_app(config)

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
