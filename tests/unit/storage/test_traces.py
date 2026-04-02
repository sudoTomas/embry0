import asyncpg
import pytest

from legion.storage.database import DatabasePool
from legion.storage.migrations.runner import run_migrations
from legion.storage.repositories.jobs import JobsRepository
from legion.storage.repositories.traces import TracesRepository


@pytest.fixture
async def repos(pg_pool: asyncpg.Pool) -> tuple[JobsRepository, TracesRepository]:
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)
    yield JobsRepository(db), TracesRepository(db)
    await db.close()


@pytest.mark.asyncio
async def test_create_and_get_trace(repos: tuple[JobsRepository, TracesRepository]):
    jobs_repo, traces_repo = repos
    job_id = await jobs_repo.create(repo="a/b", task="Fix bug")

    trace_id = await traces_repo.create(
        job_id=job_id,
        agent_type="developer",
        model="claude-sonnet-4-6",
        result="pass",
        cost_usd=0.42,
        duration_ms=15000,
        tools_called={"Read": 5, "Edit": 2},
        result_summary="Fixed the auth bug",
    )
    assert trace_id is not None

    trace = await traces_repo.get(trace_id)
    assert trace is not None
    assert trace["agent_type"] == "developer"
    assert trace["cost_usd"] == 0.42
    assert trace["result"] == "pass"


@pytest.mark.asyncio
async def test_list_traces_by_job(repos: tuple[JobsRepository, TracesRepository]):
    jobs_repo, traces_repo = repos
    job_id = await jobs_repo.create(repo="a/b", task="Fix bug")

    await traces_repo.create(job_id=job_id, agent_type="developer", model="sonnet", result="pass")
    await traces_repo.create(job_id=job_id, agent_type="validator", model="haiku", result="pass")

    rows, total = await traces_repo.list(job_id=job_id)
    assert total == 2
    assert len(rows) == 2
