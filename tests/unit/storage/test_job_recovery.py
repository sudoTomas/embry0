"""Boot recovery of the jobs a previous orchestrator left in flight (#307),
against a real database: what is resumed, launched again, or failed.

A job the drain stopped carries ``interrupted_at``; a job a crash left behind
does not, and every recovery after such an unclean stop spends one of
``MAX_RESUME_ATTEMPTS`` so a job that takes the orchestrator down cannot loop.
"""

from __future__ import annotations

import pytest

from embry0.orchestration.checkpoint import checkpointer_context
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.jobs import JobsRepository

pytestmark = pytest.mark.requires_postgres

ORPHAN = "Orchestrator restarted — job was orphaned"
NEVER_STARTED = "Orchestrator restarted before the job started"
LAUNCH = {"runner": "workflow", "issue_id": None, "issue": {"repo": "o/r", "title": "t"}, "additional_context": ""}


@pytest.fixture
async def jobs_repo(db_with_migrations: DatabasePool) -> JobsRepository:
    return JobsRepository(db_with_migrations)


async def _running(jobs: JobsRepository, **extra: object) -> str:
    job_id = await jobs.create(repo="o/r", task="t", **extra)  # type: ignore[arg-type]
    await jobs.update(job_id, status="running")
    return job_id


async def _checkpoint(db: DatabasePool, thread_id: str) -> None:
    """A minimal saved checkpoint for ``thread_id``: all recovery asks is
    whether one exists."""
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://embry0:embry0@localhost:5432/embry0_test")
    async with checkpointer_context(url):
        pass  # creates the saver's tables
    await db.execute(
        "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata)"
        " VALUES ($1, '', 'c1', '{}'::jsonb, '{}'::jsonb)",
        thread_id,
    )


async def test_the_boot_sweep_stamps_finished_at(jobs_repo: JobsRepository) -> None:
    """RAV-1271: no job enters a terminal status without finished_at. The boot
    sweep was the one terminal write that skipped it."""
    running = await _running(jobs_repo)
    queued = await jobs_repo.create(repo="o/r", task="queued")

    await jobs_repo.fail_orphaned("ERR_ORPHANED", ORPHAN, never_started_message=NEVER_STARTED)

    for job_id in (running, queued):
        row = await jobs_repo.get(job_id)
        assert row is not None and row["status"] == "failed"
        assert row["finished_at"] is not None, f"the boot sweep failed {job_id} without stamping finished_at"


async def test_the_boot_sweep_spares_the_jobs_boot_recovers(jobs_repo: JobsRepository) -> None:
    kept = await _running(jobs_repo)
    swept = await _running(jobs_repo)

    failed = await jobs_repo.fail_orphaned("ERR_ORPHANED", ORPHAN, never_started_message=NEVER_STARTED, keep=[kept])

    assert failed == [swept], f"only the job boot does not recover may fail; failed {failed}"
    assert (await jobs_repo.get(kept))["status"] == "running"  # type: ignore[index]


async def test_a_job_a_drain_stopped_fails_as_interrupted_not_orphaned(jobs_repo: JobsRepository) -> None:
    """Deploy-killed jobs are marked as such: one boot cannot resume fails with
    its own code, not the crash one."""
    drained = await _running(jobs_repo)
    crashed = await _running(jobs_repo)
    assert await jobs_repo.mark_interrupted([drained]) == [drained]

    await jobs_repo.fail_orphaned(
        "ERR_ORPHANED",
        ORPHAN,
        never_started_message=NEVER_STARTED,
        interrupted_code="ERR_INTERRUPTED_BY_SHUTDOWN",
        interrupted_message="shut down",
    )

    drained_row, crashed_row = await jobs_repo.get(drained), await jobs_repo.get(crashed)
    assert drained_row is not None and crashed_row is not None
    assert (drained_row["error_code"], drained_row["error_message"]) == ("ERR_INTERRUPTED_BY_SHUTDOWN", "shut down"), (
        f"a job the drain stopped must read as interrupted by a shutdown, got {drained_row['error_code']}"
    )
    assert drained_row["interrupted_at"] is not None, "the drain stamp must stay on a job boot could not recover"
    assert crashed_row["error_code"] == "ERR_ORPHANED"


async def test_mark_interrupted_skips_jobs_that_already_ended(jobs_repo: JobsRepository) -> None:
    ended = await _running(jobs_repo)
    await jobs_repo.update(ended, status="completed")

    assert await jobs_repo.mark_interrupted([ended]) == []
    assert (await jobs_repo.get(ended))["interrupted_at"] is None  # type: ignore[index]


async def test_launch_record_round_trips(jobs_repo: JobsRepository) -> None:
    job_id = await jobs_repo.create(repo="o/r", task="t")
    await jobs_repo.record_launch(job_id, LAUNCH)

    rows = await jobs_repo.inflight_for_recovery()

    assert [r["launch"] for r in rows if r["job_id"] == job_id] == [LAUNCH]


@pytest.mark.changes_schema  # the checkpointer creates its tables
async def test_boot_resumes_a_checkpointed_job_and_fails_an_orphan_without_state(
    db_with_migrations: DatabasePool, jobs_repo: JobsRepository
) -> None:
    resumable = await _running(jobs_repo)
    await _checkpoint(db_with_migrations, resumable)
    stateless = await _running(jobs_repo)  # no checkpoint, no launch record
    deferred = await jobs_repo.create(repo="o/r", task="deferred")
    await jobs_repo.record_launch(deferred, LAUNCH)
    await jobs_repo.mark_interrupted([deferred])

    from embry0.orchestration.restart import take_back_jobs

    recovered, exhausted = await take_back_jobs(jobs_repo, db_with_migrations, max_attempts=3)
    failed = await jobs_repo.fail_orphaned(
        "ERR_ORPHANED", ORPHAN, never_started_message=NEVER_STARTED, keep=[j["job_id"] for j in recovered]
    )

    assert {j["job_id"]: j["action"] for j in recovered} == {resumable: "resume", deferred: "relaunch"}, (
        f"boot must resume the checkpointed job and launch the deferred one again; it took back {recovered}"
    )
    assert exhausted == []
    assert failed == [stateless], f"a running job with no checkpoint and no launch record must fail; failed {failed}"
    stateless_row = await jobs_repo.get(stateless)
    assert stateless_row is not None
    assert stateless_row["error_code"] == "ERR_ORPHANED" and stateless_row["finished_at"] is not None
    resumed_row, deferred_row = await jobs_repo.get(resumable), await jobs_repo.get(deferred)
    assert resumed_row is not None and deferred_row is not None
    assert resumed_row["resume_attempts"] == 1, "a recovery after a crash (no drain stamp) spends an attempt"
    assert deferred_row["resume_attempts"] == 0, "a recovery after a clean drain must not spend an attempt"
    assert deferred_row["interrupted_at"] is None, "recovery clears the drain stamp, so the next stop is judged anew"


@pytest.mark.changes_schema  # the checkpointer creates its tables
async def test_boot_stops_resuming_a_job_that_keeps_crashing_the_orchestrator(
    db_with_migrations: DatabasePool, jobs_repo: JobsRepository
) -> None:
    from embry0.orchestration.restart import take_back_jobs

    job_id = await _running(jobs_repo)
    await _checkpoint(db_with_migrations, job_id)

    for boot in range(1, 4):
        recovered, exhausted = await take_back_jobs(jobs_repo, db_with_migrations, max_attempts=3)
        assert [j["job_id"] for j in recovered] == [job_id] and exhausted == [], f"boot {boot} must still resume it"

    recovered, exhausted = await take_back_jobs(jobs_repo, db_with_migrations, max_attempts=3)

    assert recovered == [] and exhausted == [job_id], (
        f"after 3 unclean restarts the job must not be resumed again; recovered={recovered} exhausted={exhausted}"
    )
    row = await jobs_repo.get(job_id)
    assert row is not None
    assert (row["status"], row["error_code"]) == ("failed", "ERR_RESUME_LIMIT")
    assert row["finished_at"] is not None


@pytest.mark.changes_schema  # the checkpointer creates its tables
async def test_clean_drains_never_use_up_the_attempts(
    db_with_migrations: DatabasePool, jobs_repo: JobsRepository
) -> None:
    """A long job may cross any number of deploys."""
    from embry0.orchestration.restart import take_back_jobs

    job_id = await _running(jobs_repo)
    await _checkpoint(db_with_migrations, job_id)

    for _deploy in range(5):
        await jobs_repo.mark_interrupted([job_id])
        recovered, exhausted = await take_back_jobs(jobs_repo, db_with_migrations, max_attempts=1)
        assert [j["job_id"] for j in recovered] == [job_id] and exhausted == []

    assert (await jobs_repo.get(job_id))["resume_attempts"] == 0  # type: ignore[index]
