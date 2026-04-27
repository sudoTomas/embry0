import asyncpg
import pytest

from athanor.storage.database import DatabasePool
from athanor.storage.migrations.runner import run_migrations
from athanor.storage.repositories.budget_config import BudgetConfigRepository


@pytest.fixture
async def budget_repo(pg_pool: asyncpg.Pool) -> BudgetConfigRepository:
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://athanor:athanor@localhost:5432/athanor_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)
    yield BudgetConfigRepository(db)
    await db.close()


@pytest.mark.asyncio
async def test_get_defaults(budget_repo: BudgetConfigRepository, monkeypatch):
    # budget_config has no seed row, so get() always falls through to env defaults.
    # Clear env so the hardcoded fallbacks are exercised.
    for var in (
        "MAX_BUDGET_USD",
        "DAILY_BUDGET_CAP_USD",
        "MONTHLY_BUDGET_CAP_USD",
        "RATE_LIMIT_PER_AUTHOR_PER_HOUR",
        "BUDGET_OVERRUN_MODE",
    ):
        monkeypatch.delenv(var, raising=False)
    config = await budget_repo.get()
    assert config["max_budget_per_job_usd"] == 10.0
    assert config["daily_cap_usd"] == 100.0
    assert config["monthly_cap_usd"] == 500.0
    assert config["overrun_mode"] == "soft"


@pytest.mark.asyncio
async def test_update_budget(budget_repo: BudgetConfigRepository, monkeypatch):
    monkeypatch.delenv("DAILY_BUDGET_CAP_USD", raising=False)
    await budget_repo.update(max_budget_per_job_usd=25.0, overrun_mode="hard")
    config = await budget_repo.get()
    assert config["max_budget_per_job_usd"] == 25.0
    assert config["overrun_mode"] == "hard"
    assert config["daily_cap_usd"] == 100.0


# --- Env-derived defaults ---


@pytest.mark.asyncio
async def test_get_reads_from_env_when_no_row(budget_repo: BudgetConfigRepository, monkeypatch):
    monkeypatch.setenv("MAX_BUDGET_USD", "42.5")
    monkeypatch.setenv("DAILY_BUDGET_CAP_USD", "250.0")
    monkeypatch.setenv("MONTHLY_BUDGET_CAP_USD", "1500.0")
    monkeypatch.setenv("RATE_LIMIT_PER_AUTHOR_PER_HOUR", "12")
    monkeypatch.setenv("BUDGET_OVERRUN_MODE", "hard")

    config = await budget_repo.get()
    assert config["max_budget_per_job_usd"] == 42.5
    assert config["daily_cap_usd"] == 250.0
    assert config["monthly_cap_usd"] == 1500.0
    assert config["rate_limit_per_author_per_hour"] == 12
    assert config["overrun_mode"] == "hard"


@pytest.mark.asyncio
async def test_get_invalid_env_falls_back(budget_repo: BudgetConfigRepository, monkeypatch):
    monkeypatch.setenv("MAX_BUDGET_USD", "not-a-number")
    monkeypatch.setenv("BUDGET_OVERRUN_MODE", "explosive")
    monkeypatch.delenv("DAILY_BUDGET_CAP_USD", raising=False)
    config = await budget_repo.get()
    # Falls back to hardcoded defaults on parse failure
    assert config["max_budget_per_job_usd"] == 10.0
    assert config["overrun_mode"] == "soft"


@pytest.mark.asyncio
async def test_db_row_overrides_env(budget_repo: BudgetConfigRepository, monkeypatch):
    monkeypatch.setenv("MAX_BUDGET_USD", "999.9")
    await budget_repo.update(max_budget_per_job_usd=20.0)
    config = await budget_repo.get()
    # DB wins
    assert config["max_budget_per_job_usd"] == 20.0
