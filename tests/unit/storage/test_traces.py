import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.jobs import JobsRepository
from embry0.storage.repositories.traces import TracesRepository

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def repos(db_with_migrations: DatabasePool) -> tuple[JobsRepository, TracesRepository]:
    return JobsRepository(db_with_migrations), TracesRepository(db_with_migrations)


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
    assert trace_id.startswith("trc-"), f"Expected trc- prefix, got {trace_id!r}"

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

    rows, total = await traces_repo.list_all(job_id=job_id)
    assert total == 2
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_sum_by_agent_for_job_aggregates_costs(repos: tuple[JobsRepository, TracesRepository]):
    """sum_by_agent_for_job groups by (agent_type, model) with cost/duration sums,
    ordered by cost DESC."""
    jobs_repo, traces_repo = repos
    job_id = await jobs_repo.create(repo="o/r", task="t")

    await traces_repo.create(
        job_id=job_id,
        agent_type="developer",
        model="claude-sonnet-4-6",
        result="success",
        cost_usd=0.10,
        duration_ms=1000,
    )
    await traces_repo.create(
        job_id=job_id,
        agent_type="developer",
        model="claude-sonnet-4-6",
        result="success",
        cost_usd=0.20,
        duration_ms=2000,
    )
    await traces_repo.create(
        job_id=job_id,
        agent_type="review",
        model="claude-opus-4-6",
        result="success",
        cost_usd=0.50,
        duration_ms=500,
    )

    breakdown = await traces_repo.sum_by_agent_for_job(job_id)
    assert len(breakdown) == 2
    # Ordered by cost_usd DESC: review (0.50) first, then developer (0.30)
    assert breakdown[0]["agent_type"] == "review"
    assert float(breakdown[0]["cost_usd"]) == pytest.approx(0.50)
    assert breakdown[0]["runs"] == 1
    assert breakdown[1]["agent_type"] == "developer"
    assert breakdown[1]["runs"] == 2
    assert float(breakdown[1]["cost_usd"]) == pytest.approx(0.30)
    assert breakdown[1]["duration_ms"] == 3000


@pytest.mark.asyncio
async def test_sum_by_agent_for_job_empty_returns_empty(repos: tuple[JobsRepository, TracesRepository]):
    """No traces for a job returns an empty breakdown list."""
    jobs_repo, traces_repo = repos
    job_id = await jobs_repo.create(repo="o/r", task="t")
    breakdown = await traces_repo.sum_by_agent_for_job(job_id)
    assert breakdown == []
