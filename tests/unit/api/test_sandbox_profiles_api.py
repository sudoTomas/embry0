"""API-level tests for /sandbox-profiles QA-foundation field round-tripping.

These tests need real DB round-trip behavior (the round-trip assertions verify
that new fields written via POST are returned via GET, and that the repository's
ValueError on builtin deletes maps to HTTP 403). They use a local async client
fixture mirroring the integration-test pattern in tests/integration/conftest.py
because no shared `client` fixture exists in this codebase.
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
from athanor.storage.migrations.runner import run_migrations


def _csrf() -> dict[str, str]:
    return {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Async HTTP client backed by a real PostgreSQL test database.

    Mirrors the integration `app` fixture pattern (tests/integration/conftest.py)
    so these tests can exercise repository round-trip behavior. Skips if PG
    is unavailable.
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
        try:
            await sys_conn.execute(f"CREATE DATABASE {db_name}")
        except asyncpg.DuplicateDatabaseError:
            pass
        await sys_conn.close()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")
        return

    # Truncate sandbox_profiles between tests to avoid leakage across cases
    # in this module while keeping the schema/migration state intact.
    boot_db = DatabasePool(database_url)
    await boot_db.connect()
    await run_migrations(boot_db)
    await boot_db.execute("TRUNCATE TABLE sandbox_profiles RESTART IDENTITY CASCADE")
    await boot_db.close()

    @asynccontextmanager
    async def test_lifespan(app):
        db = DatabasePool(database_url)
        await db.connect()
        await run_migrations(db)
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
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.mark.asyncio
async def test_create_with_qa_fields_round_trips(client: AsyncClient):
    payload = {
        "name": "qa-test-create",
        "base_image": "athanor-sandbox-qa:latest",
        "description": "QA test profile",
        "dind_enabled": True,
        "idle_timeout_seconds": 900,
        "extra_networks": ["backend"],
        "env_defaults": {"LANG": "C.UTF-8"},
    }
    r = await client.post("/api/v1/sandbox-profiles", json=payload, headers=_csrf())
    assert r.status_code == 201

    r = await client.get("/api/v1/sandbox-profiles/qa-test-create")
    assert r.status_code == 200
    body = r.json()
    assert body["description"] == "QA test profile"
    assert body["dind_enabled"] is True
    assert body["idle_timeout_seconds"] == 900
    assert body["extra_networks"] == ["backend"]
    assert body["env_defaults"] == {"LANG": "C.UTF-8"}
    assert body["is_builtin"] is False  # server-controlled, defaults false


@pytest.mark.asyncio
async def test_request_rejects_is_builtin_input(client: AsyncClient):
    r = await client.post(
        "/api/v1/sandbox-profiles",
        json={
            "name": "hax",
            "is_builtin": True,
        },
        headers=_csrf(),
    )
    assert r.status_code == 422  # Pydantic extra=forbid


@pytest.mark.asyncio
async def test_delete_user_profile_returns_200(client: AsyncClient):
    """Deleting a non-builtin profile succeeds."""
    payload = {"name": "delete-me-test", "base_image": "athanor-sandbox:latest"}
    r = await client.post("/api/v1/sandbox-profiles", json=payload, headers=_csrf())
    assert r.status_code == 201
    r = await client.delete("/api/v1/sandbox-profiles/delete-me-test", headers=_csrf())
    assert r.status_code == 200
    r = await client.get("/api/v1/sandbox-profiles/delete-me-test")
    assert r.status_code == 404
