import asyncpg
import pytest

from legion.storage.database import DatabasePool
from legion.storage.migrations.runner import run_migrations
from legion.storage.repositories.budget_config import BudgetConfigRepository


@pytest.fixture
async def budget_repo(pg_pool: asyncpg.Pool) -> BudgetConfigRepository:
    import os
    url = os.environ.get("TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)
    yield BudgetConfigRepository(db)
    await db.close()


@pytest.mark.asyncio
async def test_get_defaults(budget_repo: BudgetConfigRepository):
    config = await budget_repo.get()
    assert config["max_budget_per_job_usd"] == 10.0
    assert config["daily_cap_usd"] == 100.0
    assert config["monthly_cap_usd"] == 500.0
    assert config["overrun_mode"] == "soft"


@pytest.mark.asyncio
async def test_update_budget(budget_repo: BudgetConfigRepository):
    await budget_repo.update(max_budget_per_job_usd=25.0, overrun_mode="hard")
    config = await budget_repo.get()
    assert config["max_budget_per_job_usd"] == 25.0
    assert config["overrun_mode"] == "hard"
    assert config["daily_cap_usd"] == 100.0
