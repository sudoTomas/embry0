"""Integration test fixtures — requires running PostgreSQL."""

import asyncio
import os
import shutil
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import _init_app_state, create_app
from embry0.config import Embry0Config
from embry0.storage.database import DatabasePool
from embry0.storage.encryption import FernetSecretsProvider
from embry0.storage.migrations.runner import run_migrations


@pytest.fixture(scope="session")
def database_url() -> str:
    return os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://embry0:embry0@localhost:5432/embry0_integration_test",
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

        # Set the secrets provider before _init_app_state so env PUT routes
        # (which read request.app.state.secrets_provider) have a valid Fernet
        # provider in tests. Production wires this in the real lifespan.
        app.state.secrets_provider = FernetSecretsProvider(secret_key="test-secret-key")

        # Default qa_minio to None for tests that don't need a live MinIO.
        # The qa_minio_seeded fixture overrides this on demand.
        app.state.qa_minio = None

        await _init_app_state(app, db, database_url=database_url)

        # _init_app_state leaves app.state.background_tasks to the caller (see
        # its docstring). Without this wiring the executor keeps its private
        # task set, teardown never cancels fire-and-forget dispatch tasks, and
        # tests that dispatch jobs leak pending tasks that surface as
        # PytestUnraisableExceptionWarning at interpreter shutdown (RAV-639).
        app.state.background_tasks = set()
        app.state.issue_executor._background_tasks = app.state.background_tasks

        yield

        # Snapshot before cancelling — done-callbacks discard from the set.
        tasks = list(app.state.background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await db.close()

    return test_lifespan


@pytest.fixture
async def app(setup_database: str) -> AsyncIterator[AsyncClient]:
    """Create a test FastAPI app with real database (no Docker/proxy)."""
    config = Embry0Config(
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


def _is_docker_available() -> bool:
    """Return True when Docker is installed and the daemon is reachable."""
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0


def pytest_collection_modifyitems(items: list[Any], config: Any) -> None:
    """Auto-skip any test marked requires_docker when Docker is unavailable."""
    if _is_docker_available():
        return
    skip_marker = pytest.mark.skip(reason="Docker not available or daemon not reachable")
    for item in items:
        if item.get_closest_marker("requires_docker"):
            item.add_marker(skip_marker)


@pytest.fixture
async def builtin_profile_seeded(app: AsyncClient, database_url: str) -> AsyncIterator[None]:
    """Seed builtin sandbox profiles (slim, qa-jvm) into the integration test DB.

    The integration test lifespan does NOT seed builtins (the production
    lifespan in embry0/api/app.py does, but the test lifespan stops at
    _init_app_state). Tests that depend on builtin profiles being present
    request this fixture; it opens a short-lived pool to the same
    TEST_DATABASE_URL and runs the seed. The test app's own pool sees the
    rows because they share one Postgres database. Seeding is idempotent.
    """
    from embry0.storage.repositories.sandbox_profiles import SandboxProfilesRepository
    from embry0.storage.seeds.sandbox_profiles_builtin import seed_builtin_sandbox_profiles

    seed_db = DatabasePool(database_url)
    await seed_db.connect()
    try:
        await seed_builtin_sandbox_profiles(SandboxProfilesRepository(seed_db))
        yield
    finally:
        await seed_db.close()


@pytest.fixture
async def qa_minio_seeded(app):
    """Provisions qa-artifacts bucket in the test MinIO and wires the client
    into ``app.state.qa_minio`` so the dashboard routes that resolve via
    ``Depends(get_qa_minio)`` see a live client (the test_lifespan defaults
    it to None for tests that don't need MinIO).

    Skip if MINIO_ENDPOINT isn't set.
    """
    import os

    endpoint = os.environ.get("MINIO_ENDPOINT", "")
    if not endpoint:
        pytest.skip("MINIO_ENDPOINT not set")
    from embry0.execution.qa.minio_client import QAMinioClient

    client = QAMinioClient(
        endpoint=endpoint,
        access_key=os.environ["MINIO_ROOT_USER"],
        secret_key=os.environ["MINIO_ROOT_PASSWORD"],
        secure=False,
    )
    await client.ensure_bucket("qa-artifacts")

    # Inject into the app's state so routes can resolve it via the
    # get_qa_minio dependency. ``app`` here is an httpx.AsyncClient whose
    # ASGITransport holds the FastAPI app instance.
    fastapi_app = app._transport.app  # noqa: SLF001
    previous = getattr(fastapi_app.state, "qa_minio", None)
    fastapi_app.state.qa_minio = client
    try:
        yield client
    finally:
        fastapi_app.state.qa_minio = previous
