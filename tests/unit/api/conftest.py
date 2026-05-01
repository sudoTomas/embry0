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

from athanor.api.app import _init_app_state, create_app
from athanor.config import AthanorConfig
from athanor.storage.database import DatabasePool
from athanor.storage.encryption import FernetSecretsProvider
from athanor.storage.migrations.runner import run_migrations


@pytest.fixture
async def api_client() -> AsyncIterator[AsyncClient]:
    """Async HTTP client backed by a real PostgreSQL test database.

    Mirrors the integration `app` fixture pattern (tests/integration/conftest.py).
    Provisions/migrates the dedicated `athanor_unit_api_test` database, then
    TRUNCATEs the tables that API unit tests touch so each test starts clean.
    Skips cleanly when PostgreSQL is unavailable.

    Default headers include the `X-Requested-With` CSRF marker — tests should
    not need to set it on each mutating call.
    """
    database_url = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://athanor:athanor@localhost:5432/athanor_unit_api_test",
    )

    # Ensure the test database exists and migrations are applied.
    try:
        sys_url = database_url.rsplit("/", 1)[0] + "/postgres"
        sys_conn = await asyncpg.connect(sys_url)
        db_name = database_url.rsplit("/", 1)[1]
        exists = await sys_conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
        if not exists:
            try:
                # Use template0 to avoid collation-version errors on systems
                # whose template1 has a stale collation version recorded.
                await sys_conn.execute(f"CREATE DATABASE {db_name} TEMPLATE template0")
            except asyncpg.DuplicateDatabaseError:
                pass
        await sys_conn.close()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")
        return

    # Truncate tables touched by API unit tests so each test starts clean,
    # while keeping the schema/migration state intact.
    boot_db = DatabasePool(database_url)
    await boot_db.connect()
    await run_migrations(boot_db)
    await boot_db.execute(
        "TRUNCATE TABLE sandbox_profiles, global_environment, repo_environment RESTART IDENTITY CASCADE"
    )
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

    config = AthanorConfig(
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
            yield c


@pytest.fixture
async def builtin_profile_seeded(api_client: AsyncClient) -> AsyncIterator[None]:
    """Seed builtin sandbox profiles before the test runs.

    Use in tests that depend on 'slim' or 'qa-jvm' being present.

    Depends on `api_client` so the test DB exists, has migrations applied, and
    has had `sandbox_profiles` truncated. Opens a short-lived DatabasePool to
    the same TEST_DATABASE_URL and runs the seed against it; the test app's
    own pool sees the seeded rows because they share one Postgres database.
    """
    from athanor.storage.repositories.sandbox_profiles import SandboxProfilesRepository
    from athanor.storage.seeds.sandbox_profiles_builtin import seed_builtin_sandbox_profiles

    database_url = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://athanor:athanor@localhost:5432/athanor_unit_api_test",
    )
    seed_db = DatabasePool(database_url)
    await seed_db.connect()
    try:
        await seed_builtin_sandbox_profiles(SandboxProfilesRepository(seed_db))
        yield
    finally:
        await seed_db.close()
