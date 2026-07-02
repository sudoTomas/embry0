import pytest

from athanor.storage.database import DatabasePool
from athanor.storage.repositories.jobs import JobsRepository, StatusTransitionConflict
from athanor.storage.schemas import JobStatus

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def jobs_repo(db_with_migrations: DatabasePool) -> JobsRepository:
    return JobsRepository(db_with_migrations)


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

    rows, total = await jobs_repo.list_all(repo="a/b")
    assert total >= 2
    assert len(rows) >= 2


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


@pytest.mark.asyncio
async def test_append_log_event_returns_monotonic_seq(jobs_repo):
    """append_log_event returns the inserted row's id. Successive inserts are
    monotonically increasing — the id is the event_seq the WS client will use
    as a cursor."""
    job_id = await jobs_repo.create(repo="o/r", task="t")
    seq1 = await jobs_repo.append_log_event(job_id, {"type": "progress", "n": 1})
    seq2 = await jobs_repo.append_log_event(job_id, {"type": "progress", "n": 2})
    seq3 = await jobs_repo.append_log_event(job_id, {"type": "progress", "n": 3})

    assert isinstance(seq1, int) and seq1 > 0
    assert seq2 > seq1
    assert seq3 > seq2


def test_status_transition_conflict_is_runtime_error():
    """StatusTransitionConflict must subclass RuntimeError (spec §4.6)."""
    exc = StatusTransitionConflict("test message")
    assert isinstance(exc, RuntimeError)
    assert "test message" in str(exc)


@pytest.mark.asyncio
async def test_cas_valid_transition_succeeds(jobs_repo: JobsRepository):
    """A valid status transition applies correctly via the CAS path."""
    job_id = await jobs_repo.create(repo="test/repo", task="cas test")
    await jobs_repo.update(job_id, status="running")
    result = await jobs_repo.get(job_id)
    assert result is not None
    assert result["status"] == "running"

    await jobs_repo.update(job_id, status="completed")
    result = await jobs_repo.get(job_id)
    assert result is not None
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_get_log_events_filters_by_since_seq(jobs_repo):
    """get_log_events(..., since_seq=N) returns only rows with id > N.

    Enables the WS replay cursor — client reconnects with its last-seen seq
    and only gets newer events."""
    job_id = await jobs_repo.create(repo="o/r", task="t")
    seq1 = await jobs_repo.append_log_event(job_id, {"type": "progress", "n": 1})
    seq2 = await jobs_repo.append_log_event(job_id, {"type": "progress", "n": 2})
    seq3 = await jobs_repo.append_log_event(job_id, {"type": "progress", "n": 3})

    all_events = await jobs_repo.get_log_events(job_id)
    assert len(all_events) == 3

    # since_seq=seq1 — return only events after the first (seq2 and seq3)
    after_first = await jobs_repo.get_log_events(job_id, since_seq=seq1)
    assert len(after_first) == 2
    returned_ids = {e["id"] for e in after_first}
    assert returned_ids == {seq2, seq3}

    # since_seq=seq3 — return nothing (nothing newer)
    after_last = await jobs_repo.get_log_events(job_id, since_seq=seq3)
    assert after_last == []


@pytest.mark.asyncio
async def test_create_derives_git_context_from_repo(jobs_repo):
    job_id = await jobs_repo.create(repo="owner/repo", task="Fix the bug")
    job = await jobs_repo.get(job_id)
    assert job["repo"] == "owner/repo"
    assert job["context"] == {"type": "git", "repo": "owner/repo"}


@pytest.mark.asyncio
async def test_create_repo_less_job_with_context(jobs_repo):
    job_id = await jobs_repo.create(task="Research the market", context={"type": "none"})
    job = await jobs_repo.get(job_id)
    assert job["repo"] is None
    assert job["context"] == {"type": "none"}


@pytest.mark.asyncio
async def test_create_derives_none_context_when_repo_and_context_omitted(jobs_repo):
    job_id = await jobs_repo.create(task="Research the market")
    job = await jobs_repo.get(job_id)
    assert job["repo"] is None
    assert job["context"] == {"type": "none"}
