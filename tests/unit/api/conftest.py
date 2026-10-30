"""Shared fixtures for tests/unit/api/.

Provides an `api_client` fixture: an httpx.AsyncClient backed by the FastAPI
app, wired to a real PostgreSQL test database with all migrations applied.

The fixture is shared across the api unit tests so individual tests don't
need to repeat the ~50 lines of bootstrap. Default headers include
`X-Requested-With: XMLHttpRequest`, so tests don't have to remember to add
the CSRF header on every mutating call.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import _init_app_state, create_app
from embry0.config import Embry0Config
from embry0.storage.database import DatabasePool
from embry0.storage.encryption import FernetSecretsProvider
from embry0.storage.migrations.runner import run_migrations
from tests.fixtures.postgres import postgres_unavailable, quoted, server_and_database, start_clean


@pytest.fixture
async def api_client(request: pytest.FixtureRequest) -> AsyncIterator[AsyncClient]:
    """Async HTTP client backed by a real PostgreSQL test database.

    Mirrors the integration `app` fixture pattern (tests/integration/conftest.py).
    Provisions the dedicated `embry0_unit_api_test` database and resets it with
    `start_clean`: migrated once, then emptied and given back what the
    migrations wrote before each test, so each test starts with those rows and
    no earlier test's (#235); a test marked `changes_schema` gets the schema
    migrated from empty. Skips cleanly when PostgreSQL is unavailable.

    Default headers include the `X-Requested-With` CSRF marker — tests should
    not need to set it on each mutating call.
    """
    database_url = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://embry0:embry0@localhost:5432/embry0_unit_api_test",
    )

    # Ensure the test database exists and migrations are applied.
    try:
        sys_url, db_name = server_and_database(database_url)
        sys_conn = await asyncpg.connect(sys_url)
        exists = await sys_conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
        if not exists:
            try:
                # Use template0 to avoid collation-version errors on systems
                # whose template1 has a stale collation version recorded.
                await sys_conn.execute(f"CREATE DATABASE {quoted(db_name)} TEMPLATE template0")
            except asyncpg.DuplicateDatabaseError:
                pass
        await sys_conn.close()
    except (OSError, asyncpg.PostgresError) as exc:
        postgres_unavailable(exc)
        return

    # Start from the migrations alone. A list of tables to empty left every
    # other table as the last test left it, and the next test to read one saw
    # its rows (#235).
    boot_db = DatabasePool(database_url)
    await boot_db.connect()
    try:
        await start_clean(boot_db, database_url, request.node)
    finally:
        await boot_db.close()

    @asynccontextmanager
    async def test_lifespan(app):
        db = DatabasePool(database_url)
        await db.connect()
        await run_migrations(db)
        # Set the secrets provider before _init_app_state so IssueExecutor (and
        # any environment endpoint that reads request.app.state.secrets_provider)
        # has a valid Fernet provider. Production wires this in the real lifespan;
        # tests only exercise _init_app_state, so we set it here directly.
        app.state.secrets_provider = FernetSecretsProvider(secret_key="test-secret-key")
        await _init_app_state(app, db, database_url=database_url)
        yield
        await db.close()

    config = Embry0Config(
        _env_file=None,
        database_url=database_url,
        auth_dev_mode=True,
        webhook_dev_mode=True,
        api_key="",
        github_webhook_secret="test-secret",
    )
    fastapi_app = create_app(config, lifespan_override=test_lifespan)

    async with test_lifespan(fastapi_app):
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ) as c:
            # Expose the underlying FastAPI app on the client so tests can
            # mutate app.state for fixture-side wiring (e.g. injecting a
            # fake QA MinIO client / sandbox token registry).
            c.app = fastapi_app
            yield c


@pytest.fixture
async def builtin_profile_seeded(api_client: AsyncClient) -> AsyncIterator[None]:
    """Seed builtin sandbox profiles before the test runs.

    Use in tests that depend on 'slim' or 'qa-jvm' being present.

    Depends on `api_client` so the test DB exists, as the migrations leave it,
    with no sandbox profile in it. Opens a short-lived DatabasePool to
    the same TEST_DATABASE_URL and runs the seed against it; the test app's
    own pool sees the seeded rows because they share one Postgres database.
    """
    from embry0.storage.repositories.sandbox_profiles import SandboxProfilesRepository
    from embry0.storage.seeds.sandbox_profiles_builtin import seed_builtin_sandbox_profiles

    database_url = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://embry0:embry0@localhost:5432/embry0_unit_api_test",
    )
    seed_db = DatabasePool(database_url)
    await seed_db.connect()
    try:
        await seed_builtin_sandbox_profiles(SandboxProfilesRepository(seed_db))
        yield
    finally:
        await seed_db.close()
