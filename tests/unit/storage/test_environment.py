"""Integration tests for EnvironmentRepository against real postgres."""

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.environment import EnvironmentRepository

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def env_repo(db_with_migrations: DatabasePool) -> EnvironmentRepository:
    return EnvironmentRepository(db_with_migrations)


@pytest.mark.asyncio
async def test_set_and_get_global(env_repo: EnvironmentRepository):
    await env_repo.set_global(
        [
            {"key": "MODE", "value": "prod", "var_type": "config", "description": "env mode"},
            {"key": "API_TOKEN", "value": "ciphertext-1", "var_type": "secret", "description": ""},
        ]
    )
    rows = await env_repo.get_global()
    assert len(rows) >= 2
    keys = {r["key"] for r in rows}
    assert "MODE" in keys
    assert "API_TOKEN" in keys
    token = next(r for r in rows if r["key"] == "API_TOKEN")
    assert token["var_type"] == "secret"


@pytest.mark.asyncio
async def test_set_global_replaces_all(env_repo: EnvironmentRepository):
    await env_repo.set_global([{"key": "OLD_UNIQUE_KEY", "value": "1"}])
    await env_repo.set_global([{"key": "NEW_UNIQUE_KEY", "value": "2"}])
    rows = await env_repo.get_global()
    keys = {r["key"] for r in rows}
    assert "NEW_UNIQUE_KEY" in keys
    assert "OLD_UNIQUE_KEY" not in keys


@pytest.mark.asyncio
async def test_set_and_get_repo_filters(env_repo: EnvironmentRepository):
    await env_repo.set_repo(
        "foo/bar-unique",
        [{"key": "A", "value": "1", "required": True}],
    )
    await env_repo.set_repo(
        "baz/qux-unique",
        [{"key": "B", "value": "2"}],
    )
    foo_rows = await env_repo.get_repo("foo/bar-unique")
    baz_rows = await env_repo.get_repo("baz/qux-unique")
    assert {r["key"] for r in foo_rows} == {"A"}
    assert {r["key"] for r in baz_rows} == {"B"}
    assert foo_rows[0]["required"] is True


@pytest.mark.asyncio
async def test_delete_repo_var(env_repo: EnvironmentRepository):
    await env_repo.set_repo(
        "foo/bar-delete-test",
        [{"key": "A", "value": "1"}, {"key": "B", "value": "2"}],
    )
    await env_repo.delete_repo_var("foo/bar-delete-test", "A")
    rows = await env_repo.get_repo("foo/bar-delete-test")
    assert {r["key"] for r in rows} == {"B"}
