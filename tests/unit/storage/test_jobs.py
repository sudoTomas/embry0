import asyncpg
import pytest

from legion.storage.database import DatabasePool
from legion.storage.migrations.runner import run_migrations
from legion.storage.repositories.jobs import JobsRepository
from legion.storage.schemas import JobStatus


@pytest.fixture
async def jobs_repo(pg_pool: asyncpg.Pool) -> JobsRepository:
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)
    repo = JobsRepository(db)
    yield repo
    await db.close()


@pytest.mark.asyncio
async def test_create_and_get_job(jobs_repo: JobsRepository):
    job_id = await jobs_repo.create(repo="owner/repo", task="Fix the bug")
    assert job_id.startswith("job-")

    job = await jobs_repo.get(job_id)
    assert job is not None
    assert job["repo"] == "owner/repo"
    assert job["task"] == "Fix the bug"
    assert job["status"] == "pending"
    assert job["total_cost_usd"] == 0.0


@pytest.mark.asyncio
async def test_list_jobs_with_filter(jobs_repo: JobsRepository):
    await jobs_repo.create(repo="a/b", task="Task 1")
    await jobs_repo.create(repo="a/b", task="Task 2")
    await jobs_repo.create(repo="c/d", task="Task 3")

    rows, total = await jobs_repo.list(repo="a/b")
    assert total == 2
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_update_job(jobs_repo: JobsRepository):
    job_id = await jobs_repo.create(repo="a/b", task="Task")
    await jobs_repo.update(job_id, status=JobStatus.RUNNING)

    job = await jobs_repo.get(job_id)
    assert job is not None
    assert job["status"] == "running"


@pytest.mark.asyncio
async def test_increment_cost(jobs_repo: JobsRepository):
    job_id = await jobs_repo.create(repo="a/b", task="Task")
    await jobs_repo.increment_cost(job_id, 1.50)
    await jobs_repo.increment_cost(job_id, 0.75)

    job = await jobs_repo.get(job_id)
    assert job is not None
    assert abs(job["total_cost_usd"] - 2.25) < 0.001


@pytest.mark.asyncio
async def test_get_nonexistent_job(jobs_repo: JobsRepository):
    job = await jobs_repo.get("job-doesnotexist")
    assert job is None


@pytest.mark.asyncio
async def test_update_same_status_logs_debug_no_op(jobs_repo: JobsRepository, capsys):
    """update() with same status logs a debug-level 'no-op' marker for observability.

    Uses capsys (not caplog) because this project's structlog writes to stdout
    via PrintLoggerFactory. See Plan A Task 2 for the same pattern.
    """
    job_id = await jobs_repo.create(repo="o/r", task="t")
    await jobs_repo.update(job_id, status=JobStatus.RUNNING)

    # Drain any output from the first update
    capsys.readouterr()

    # Same status — should be a no-op and emit the debug marker
    await jobs_repo.update(job_id, status=JobStatus.RUNNING)

    captured = capsys.readouterr()
    assert "job_status_update_noop" in captured.out, (
        f"Expected 'job_status_update_noop' log on stdout. Got: {captured.out!r}"
    )
