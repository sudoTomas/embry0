"""Integration tests for EnvironmentRepository against real postgres."""

from collections.abc import AsyncIterator

import asyncpg
import pytest

from legion.storage.database import DatabasePool
from legion.storage.migrations.runner import run_migrations
from legion.storage.repositories.environment import EnvironmentRepository


@pytest.fixture
async def env_repo(pg_pool: asyncpg.Pool) -> AsyncIterator[EnvironmentRepository]:
    import os

    url = os.environ.get(
        "TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test"
    )
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)
    yield EnvironmentRepository(db)
    await db.close()


@pytest.mark.asyncio
async def test_set_and_get_global(env_repo: EnvironmentRepository):
    await env_repo.set_global(
        [
            {"key": "MODE", "value": "prod", "var_type": "config", "description": "env mode"},
            {"key": "API_TOKEN", "value": "ciphertext-1", "var_type": "secret", "description": ""},
        ]
    )
    rows = await env_repo.get_global()
    assert len(rows) == 2
    keys = {r["key"] for r in rows}
    assert keys == {"MODE", "API_TOKEN"}
    token = next(r for r in rows if r["key"] == "API_TOKEN")
    assert token["var_type"] == "secret"


@pytest.mark.asyncio
async def test_set_global_replaces_all(env_repo: EnvironmentRepository):
    await env_repo.set_global([{"key": "OLD", "value": "1"}])
    await env_repo.set_global([{"key": "NEW", "value": "2"}])
    rows = await env_repo.get_global()
    keys = {r["key"] for r in rows}
    assert keys == {"NEW"}


@pytest.mark.asyncio
async def test_set_and_get_repo_filters(env_repo: EnvironmentRepository):
    await env_repo.set_repo(
        "foo/bar",
        [{"key": "A", "value": "1", "required": True}],
    )
    await env_repo.set_repo(
        "baz/qux",
        [{"key": "B", "value": "2"}],
    )
    foo_rows = await env_repo.get_repo("foo/bar")
    baz_rows = await env_repo.get_repo("baz/qux")
    assert {r["key"] for r in foo_rows} == {"A"}
    assert {r["key"] for r in baz_rows} == {"B"}
    assert foo_rows[0]["required"] is True


@pytest.mark.asyncio
async def test_delete_repo_var(env_repo: EnvironmentRepository):
    await env_repo.set_repo(
        "foo/bar",
        [{"key": "A", "value": "1"}, {"key": "B", "value": "2"}],
    )
    await env_repo.delete_repo_var("foo/bar", "A")
    rows = await env_repo.get_repo("foo/bar")
    assert {r["key"] for r in rows} == {"B"}
