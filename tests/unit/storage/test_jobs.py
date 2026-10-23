import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.jobs import JobsRepository, StatusTransitionConflict
from embry0.storage.schemas import JobStatus

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
async def test_current_stage_defaults_null_and_is_updatable(jobs_repo: JobsRepository):
    """Migration 35: current_stage starts NULL (legacy-row shape) and accepts
    plain updates without a status transition."""
    job_id = await jobs_repo.create(repo="a/b", task="Task")

    job = await jobs_repo.get(job_id)
    assert job is not None
    assert job["current_stage"] is None

    await jobs_repo.update(job_id, current_stage="triage_complete")
    job = await jobs_repo.get(job_id)
    assert job is not None
    assert job["current_stage"] == "triage_complete"


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


# ---- RAV-1271: terminal statuses always stamp finished_at -------------------


@pytest.mark.asyncio
async def test_terminal_status_without_finished_at_gets_stamped(jobs_repo: JobsRepository):
    """Structural invariant: a bare update(status="completed") — the exact
    write the corrupt qa_ask_user path produced — must stamp finished_at."""
    job_id = await jobs_repo.create(repo="a/b", task="Task")
    await jobs_repo.update(job_id, status=JobStatus.RUNNING)

    await jobs_repo.update(job_id, status=JobStatus.COMPLETED)

    job = await jobs_repo.get(job_id)
    assert job is not None
    assert job["status"] == "completed"
    assert job["finished_at"] is not None


@pytest.mark.asyncio
async def test_terminal_status_keeps_caller_supplied_finished_at(jobs_repo: JobsRepository):
    """An explicit finished_at wins over the invariant stamp."""
    from datetime import UTC, datetime

    explicit = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    job_id = await jobs_repo.create(repo="a/b", task="Task")
    await jobs_repo.update(job_id, status=JobStatus.RUNNING)

    await jobs_repo.update(job_id, status=JobStatus.FAILED, finished_at=explicit)

    job = await jobs_repo.get(job_id)
    assert job is not None
    assert job["finished_at"] == explicit


@pytest.mark.asyncio
async def test_post_terminal_transition_does_not_move_finished_at(jobs_repo: JobsRepository):
    """completed → pr_merged must keep the original finished_at."""
    job_id = await jobs_repo.create(repo="a/b", task="Task")
    await jobs_repo.update(job_id, status=JobStatus.RUNNING)
    await jobs_repo.update(job_id, status=JobStatus.COMPLETED)

    first = (await jobs_repo.get(job_id))["finished_at"]
    assert first is not None

    await jobs_repo.update(job_id, status=JobStatus.PR_MERGED)
    assert (await jobs_repo.get(job_id))["finished_at"] == first


@pytest.mark.asyncio
async def test_cas_transition_to_terminal_stamps_finished_at(jobs_repo: JobsRepository):
    """cas_transition_on_conn to a terminal status stamps finished_at too."""
    job_id = await jobs_repo.create(repo="a/b", task="Task")
    await jobs_repo.update(job_id, status=JobStatus.RUNNING)

    async with jobs_repo._db.transaction() as conn:
        ok = await jobs_repo.cas_transition_on_conn(conn, job_id, JobStatus.FAILED, {JobStatus.RUNNING})
    assert ok is True

    job = await jobs_repo.get(job_id)
    assert job is not None
    assert job["status"] == "failed"
    assert job["finished_at"] is not None


@pytest.mark.asyncio
async def test_fail_orphaned_stamps_finished_at_on_every_row_it_fails(jobs_repo: JobsRepository):
    """#345: the boot-time sweep is raw SQL and must keep the invariant
    itself. Without finished_at an orphaned job has no duration, so the
    job-duration histogram never observed it."""
    queued = await jobs_repo.create(repo="a/b", task="queued")
    running = await jobs_repo.create(repo="a/b", task="running")
    await jobs_repo.update(running, status=JobStatus.RUNNING)

    await jobs_repo.fail_orphaned("ERR_ORPHANED", "restarted", never_started_message="never started")

    for job_id in (queued, running):
        job = await jobs_repo.get(job_id)
        assert job is not None and job["status"] == "failed"
        assert job["finished_at"] is not None, f"fail_orphaned left finished_at NULL on a {job['task']} job"


@pytest.mark.asyncio
async def test_paused_job_expiry_without_a_jobs_repo_stamps_finished_at(jobs_repo: JobsRepository):
    """#345: the reaper's raw-SQL expiry fallback is the other write path
    that sets a terminal status outside update()."""
    from unittest.mock import AsyncMock, MagicMock

    from embry0.execution.image_manager import ContainerReaper

    db = jobs_repo._db
    job_id = await jobs_repo.create(repo="a/b", task="paused")
    await jobs_repo.update(job_id, status=JobStatus.RUNNING)
    await jobs_repo.update(job_id, status=JobStatus.PAUSED)
    # Age it past the TTL; the trigger would overwrite a plain updated_at write.
    await db.execute("ALTER TABLE jobs DISABLE TRIGGER trg_jobs_updated_at")
    await db.execute("UPDATE jobs SET updated_at = NOW() - INTERVAL '3 hours' WHERE job_id = $1", job_id)
    await db.execute("ALTER TABLE jobs ENABLE TRIGGER trg_jobs_updated_at")
    runtime = MagicMock()
    runtime.client.run_cmd = AsyncMock()
    reaper = ContainerReaper(runtime, db=db, paused_ttl_hours=1, jobs_repo=None, sandbox_manager=AsyncMock())

    await reaper._expire_paused_jobs()

    job = await jobs_repo.get(job_id)
    assert job is not None and job["status"] == "expired"
    assert job["finished_at"] is not None, "the expiry fallback left finished_at NULL on an expired job"


def test_every_raw_terminal_job_write_stamps_finished_at():
    """#345: a terminal status must always carry finished_at, whichever
    path writes it. update() and cas_transition_on_conn() add it
    structurally; any SQL that writes a terminal job status literally has to
    name finished_at in the same statement. A new raw writer that forgets it
    fails here, not in a dashboard weeks later."""
    import re
    from pathlib import Path

    import embry0

    terminal = "|".join(
        sorted(
            s.value
            for s in (JobStatus.COMPLETED, JobStatus.PARTIAL, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.EXPIRED)
        )
    )
    # UPDATE jobs ... SET ... status = '<terminal>' ... up to the WHERE.
    statement = re.compile(
        r"UPDATE\s+jobs\b(?P<body>(?:(?!WHERE).)*?\bstatus\s*=\s*'(?:" + terminal + r")'(?:(?!WHERE).)*)WHERE",
        re.IGNORECASE | re.DOTALL,
    )
    root = Path(embry0.__file__).parent
    offenders: list[str] = []
    seen = 0
    for path in root.rglob("*.py"):
        if "migrations" in path.parts:
            continue  # history: what a migration wrote is fixed, and 88 backfills it
        # Join adjacent string literals so a statement split across lines reads whole.
        text = re.sub(r"\"\s*\n\s*\"", "", path.read_text())
        for match in statement.finditer(text):
            seen += 1
            if "finished_at" not in match.group("body"):
                offenders.append(f"{path.relative_to(root)}: {' '.join(match.group(0).split())[:160]}")
    assert seen >= 2, f"the scan found only {seen} raw terminal writes; it is no longer measuring anything"
    assert not offenders, "raw SQL sets a terminal job status without finished_at:\n" + "\n".join(offenders)


@pytest.mark.asyncio
async def test_fetch_statuses_returns_only_known_jobs(jobs_repo: JobsRepository):
    """#274: the volume reaper maps owning job ids to statuses; an id with no
    row must be absent (not defaulted) so it reads as an unknown owner."""
    running = await jobs_repo.create(repo="a/b", task="t1")
    await jobs_repo.update(running, status="running")
    pending = await jobs_repo.create(repo="a/b", task="t2")

    statuses = await jobs_repo.fetch_statuses([running, pending, "job-doesnotexist"])

    assert statuses == {running: "running", pending: "pending"}


@pytest.mark.asyncio
async def test_fetch_statuses_empty_input(jobs_repo: JobsRepository):
    assert await jobs_repo.fetch_statuses([]) == {}


@pytest.mark.asyncio
async def test_fail_orphaned_says_a_queued_job_never_started(jobs_repo: JobsRepository):
    """#583: a job can now wait in `pending` for a run slot. A restart fails
    it with the other orphans, and its message must not claim it was running."""
    queued = await jobs_repo.create(repo="owner/repo", task="queued")
    running = await jobs_repo.create(repo="owner/repo", task="running")
    await jobs_repo.update(running, status="running")

    ids = await jobs_repo.fail_orphaned(
        "ERR_ORPHANED", "Orchestrator restarted", never_started_message="Restarted before the job started"
    )

    assert set(ids) == {queued, running}
    queued_row, running_row = await jobs_repo.get(queued), await jobs_repo.get(running)
    assert queued_row is not None and running_row is not None
    assert queued_row["error_message"] == "Restarted before the job started", (
        f"a job that never left pending got {queued_row['error_message']!r}"
    )
    assert running_row["error_message"] == "Orchestrator restarted"
    assert {queued_row["error_code"], running_row["error_code"]} == {"ERR_ORPHANED"}


# ---- #348: the gate refusal is the one pending -> failed transition ----------


@pytest.mark.asyncio
async def test_a_budget_refusal_may_fail_a_pending_job(jobs_repo: JobsRepository):
    job_id = await jobs_repo.create(repo="a/b", task="t")

    await jobs_repo.update(job_id, status=JobStatus.FAILED, error_code="ERR_BUDGET_REFUSED", error_message="refused")

    job = await jobs_repo.get(job_id)
    assert job is not None
    assert (job["status"], job["started_at"]) == ("failed", None)
    assert job["finished_at"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("error_code", [None, "ERR_BUDGET_OVERRUN", "ERR_UNKNOWN"])
async def test_no_other_failure_may_skip_running(jobs_repo: JobsRepository, error_code: str | None):
    """The exception is keyed on the refusal code; every other failure of a
    pending job is still an invalid transition."""
    job_id = await jobs_repo.create(repo="a/b", task="t")
    fields = {"error_code": error_code} if error_code else {}

    with pytest.raises(ValueError, match="pending -> failed"):
        await jobs_repo.update(job_id, status=JobStatus.FAILED, **fields)

    assert (await jobs_repo.get(job_id))["status"] == "pending"


def test_the_transition_table_itself_still_forbids_pending_to_failed():
    """The refusal is an exception beside the table, not an entry in it:
    readers of VALID_JOB_TRANSITIONS (cas_transition_on_conn, delivery
    writes) must keep seeing pending -> failed as illegal."""
    from embry0.storage.repositories.jobs import VALID_JOB_TRANSITIONS

    assert JobStatus.FAILED not in VALID_JOB_TRANSITIONS[JobStatus.PENDING]


# ---- #474: skipped is a terminal status of a job that ran ------------------


@pytest.mark.asyncio
async def test_a_running_job_can_end_skipped_and_gets_a_finish_time(jobs_repo: JobsRepository):
    job_id = await jobs_repo.create(repo="a/b", task="t")
    await jobs_repo.update(job_id, status=JobStatus.RUNNING)

    await jobs_repo.update(job_id, status=JobStatus.SKIPPED, result_summary="Skipped: …")

    job = await jobs_repo.get(job_id)
    assert job is not None and job["status"] == "skipped"
    assert job["finished_at"] is not None, "skipped is terminal and must stamp finished_at"
    assert job["delivery_state"] == "none"


@pytest.mark.asyncio
@pytest.mark.parametrize("then", [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.RUNNING, JobStatus.PR_MERGED])
async def test_skipped_is_terminal(jobs_repo: JobsRepository, then: JobStatus):
    job_id = await jobs_repo.create(repo="a/b", task="t")
    await jobs_repo.update(job_id, status=JobStatus.RUNNING)
    await jobs_repo.update(job_id, status=JobStatus.SKIPPED)

    with pytest.raises(ValueError, match="skipped ->"):
        await jobs_repo.update(job_id, status=then)
