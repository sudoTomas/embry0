"""Integration test fixtures — requires running PostgreSQL."""

import os
import shutil
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from athanor.api.app import _init_app_state, create_app
from athanor.config import AthanorConfig
from athanor.storage.database import DatabasePool
from athanor.storage.migrations.runner import run_migrations


@pytest.fixture(scope="session")
def database_url() -> str:
    return os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://athanor:athanor@localhost:5432/athanor_integration_test",
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


def _make_test_lifespan(database_url: str):
    """Return an ASGI lifespan that initialises only DB repos (no Docker/proxy)."""

    @asynccontextmanager
    async def test_lifespan(app):
        db = DatabasePool(database_url)
        await db.connect()
        await run_migrations(db)

        await _init_app_state(app, db, database_url=database_url)

        yield

        await db.close()

    return test_lifespan


@pytest.fixture
async def app(setup_database: str) -> AsyncIterator[AsyncClient]:
    """Create a test FastAPI app with real database (no Docker/proxy)."""
    config = AthanorConfig(
        _env_file=None,
        database_url=setup_database,
        auth_dev_mode=True,
        webhook_dev_mode=True,
        api_key="",
        github_webhook_secret="test-secret",
    )
    test_lifespan = _make_test_lifespan(setup_database)
    fastapi_app = create_app(config, lifespan_override=test_lifespan)

    # Manually drive the lifespan context so DB repos are available before
    # any HTTP request is made via ASGITransport (which does not send lifespan
    # events automatically).
    async with test_lifespan(fastapi_app):
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture(scope="session", autouse=False)
def docker_available() -> None:
    """Skip requires_docker tests when no Docker daemon is reachable."""
    if shutil.which("docker") is None:
        pytest.skip("Docker not available — skipping requires_docker tests")
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        timeout=10,
    )
    if result.returncode != 0:
        pytest.skip("Docker daemon not reachable — skipping requires_docker tests")
