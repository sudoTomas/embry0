"""Shared test fixtures."""

import os
import socket
from collections.abc import AsyncIterator

import asyncpg
import pytest

from athanor.storage.database import DatabasePool, _jsonb_decoder, _jsonb_encoder
from athanor.storage.migrations.runner import run_migrations


def pytest_collection_modifyitems(config, items):
    """Skip MinIO-tagged tests when no MinIO endpoint is reachable.
    Skip DinD-tagged tests when no DinD endpoint is reachable.
    """
    endpoint = os.environ.get("MINIO_ENDPOINT", "")
    minio_ok = False
    if endpoint:
        host, _, port = endpoint.partition(":")
        try:
            with socket.create_connection((host, int(port or "9000")), timeout=1):
                minio_ok = True
        except OSError:
            minio_ok = False

    dind_ok = False
    dind_endpoint = os.environ.get("DIND_ENDPOINT", "")
    if dind_endpoint:
        host, _, port = dind_endpoint.partition(":")
        try:
            with socket.create_connection((host, int(port or "2376")), timeout=1):
                dind_ok = True
        except OSError:
            dind_ok = False

    skip_minio = pytest.mark.skip(reason="MinIO not available — set MINIO_ENDPOINT")
    skip_dind = pytest.mark.skip(reason="DinD not available — set DIND_ENDPOINT")
    for item in items:
        if "requires_minio" in item.keywords and not minio_ok:
            item.add_marker(skip_minio)
        if "requires_dind" in item.keywords and not dind_ok:
            item.add_marker(skip_dind)


@pytest.fixture
async def db_with_migrations() -> AsyncIterator[DatabasePool]:
    """Function-scoped DatabasePool with a clean, fully-migrated schema.

    Requires a live PostgreSQL instance at TEST_DATABASE_URL (or the default
    athanor_test database). Tests consuming this fixture are expected to be
    marked @pytest.mark.requires_postgres.

    The public schema is dropped and recreated before migrations run, so every
    consuming test gets a clean, fully-migrated database with zero rows
    regardless of test execution order — no cross-test contamination.
    """
    import os

    url = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://athanor:athanor@localhost:5432/athanor_test",
    )
    db = DatabasePool(url, min_size=1, max_size=5)
    try:
        await db.connect()
    except Exception as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")
        return
    await db.execute("DROP SCHEMA public CASCADE")
    await db.execute("CREATE SCHEMA public")
    await run_migrations(db)
    yield db
    await db.close()


@pytest.fixture
async def pg_pool() -> AsyncIterator[asyncpg.Pool]:
    """Create a temporary test database and return a connection pool.

    Requires a running PostgreSQL instance at localhost:5432.
    Uses DATABASE_URL env var or defaults to test database.
    Falls back to skipping if PostgreSQL is unavailable.
    """
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://athanor:athanor@localhost:5432/athanor_test")

    try:
        # Create database if needed
        sys_url = url.rsplit("/", 1)[0] + "/postgres"
        sys_conn = await asyncpg.connect(sys_url)
        db_name = url.rsplit("/", 1)[1]
        try:
            await sys_conn.execute(f"CREATE DATABASE {db_name}")
        except asyncpg.DuplicateDatabaseError:
            pass
        await sys_conn.close()

        async def _init_connection(conn: asyncpg.Connection) -> None:
            # Mirror DatabasePool's JSONB codec so JSONB columns round-trip
            # as Python objects (dict/list/scalar). Without this, asyncpg
            # returns JSONB as a raw string and writes accept anything,
            # which masks repository-side encoding bugs.
            await conn.set_type_codec(
                "jsonb",
                encoder=_jsonb_encoder,
                decoder=_jsonb_decoder,
                schema="pg_catalog",
            )

        pool = await asyncpg.create_pool(
            url,
            min_size=1,
            max_size=5,
            init=_init_connection,
        )
        assert pool is not None

        yield pool

        # Cleanup tables after test
        async with pool.acquire() as conn:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
        await pool.close()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")
