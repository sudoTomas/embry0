import pytest
from athanor.storage.repositories.environment import EnvironmentRepository


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_global_round_trip_with_scope(db_with_migrations):
    repo = EnvironmentRepository(db_with_migrations)
    await repo.set_global([
        {"key": "PUBLIC_API_URL", "value": "https://x", "var_type": "config", "scope": "app"},
        {"key": "QA_TEST_USER", "value": "qa@example.com", "var_type": "config", "scope": "qa"},
    ])
    rows = await repo.get_global()
    by_key = {r["key"]: r for r in rows}
    assert by_key["PUBLIC_API_URL"]["scope"] == "app"
    assert by_key["QA_TEST_USER"]["scope"] == "qa"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_repo_round_trip_with_scope(db_with_migrations):
    repo = EnvironmentRepository(db_with_migrations)
    await repo.set_repo("owner/proj", [
        {"key": "DB_URL", "value": "...", "var_type": "secret", "scope": "app"},
        {"key": "QA_LOGIN_TOKEN", "value": "abc", "var_type": "secret", "scope": "qa"},
    ])
    rows = await repo.get_repo("owner/proj")
    by_key = {r["key"]: r for r in rows}
    assert by_key["DB_URL"]["scope"] == "app"
    assert by_key["QA_LOGIN_TOKEN"]["scope"] == "qa"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_default_scope_is_app(db_with_migrations):
    repo = EnvironmentRepository(db_with_migrations)
    await repo.set_global([{"key": "X", "value": "1", "var_type": "config"}])  # no scope
    rows = await repo.get_global()
    by_key = {r["key"]: r for r in rows}
    assert by_key["X"]["scope"] == "app"
