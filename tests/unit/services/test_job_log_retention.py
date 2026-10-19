"""Job log retention (ADR 0017): old code payloads are redacted, metadata and everything else stay."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.execution import events
from embry0.services.job_log_retention import (
    CODE_INPUT_FIELDS,
    REDACTED_MARKER,
    REDACTED_TEXT,
    JobLogRetentionSweep,
    plan_job,
    redact_event,
    tool_meta,
)
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.job_log_retention import JobLogRetentionRepository, LogRow
from embry0.storage.repositories.jobs import JobsRepository

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=40)
IDS = {"run_id": "run-1", "node": "developer", "agent": "developer"}
FILE_BODY = "def secret_algorithm():\n    return 42\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _write(tool_id: str = "t-write", path: str = "/workspace/app.py") -> dict:
    return events.tool_call(
        tool_id=tool_id,
        tool_name="Write",
        summary=path,
        tool_input={"file_path": path, "content": FILE_BODY},
        **IDS,
    )


def _read(tool_id: str = "t-read", path: str = "/workspace/app.py") -> dict:
    return events.tool_call(tool_id=tool_id, tool_name="Read", summary=path, tool_input={"file_path": path}, **IDS)


def _result(tool_id: str, content: str = FILE_BODY) -> dict:
    return events.tool_result(tool_id=tool_id, content=content, is_error=False, **IDS)


# --- the redaction rule --------------------------------------------------------


def test_a_write_loses_its_file_body_and_keeps_the_metadata() -> None:
    original = _write()
    redacted, removed = redact_event(original, tool_calls={}, redacted_at=NOW.isoformat()) or ({}, 0)

    assert redacted["tool_input"] == {"file_path": "/workspace/app.py", "content": REDACTED_TEXT}, (
        "a Write's content must be replaced and its file_path kept"
    )
    assert redacted["summary"] == "/workspace/app.py"
    record = redacted["redacted"]
    assert record["tool_name"] == "Write" and record["file_path"] == "/workspace/app.py"
    assert record["fields"] == {"content": {"bytes": len(FILE_BODY.encode()), "sha256": _sha(FILE_BODY)}}
    assert removed == len(FILE_BODY.encode())
    for key in ("type", "timestamp", "tool_id", "tool_name", "run_id", "node", "agent"):
        assert redacted[key] == original[key], f"{key} must survive redaction"
    assert original["tool_input"]["content"] == FILE_BODY, "redaction must not mutate the event it was given"


def test_edit_and_bash_inputs_are_code_and_a_bash_summary_goes_too() -> None:
    edit = events.tool_call(
        tool_id="t-edit",
        tool_name="Edit",
        summary="/workspace/a.py",
        tool_input={"file_path": "/workspace/a.py", "old_string": "x = 1", "new_string": "x = 2", "replace_all": False},
        **IDS,
    )
    command = "cat > /workspace/b.py <<'EOF'\nprint('hi')\nEOF"
    bash = events.tool_call(
        tool_id="t-bash", tool_name="Bash", summary=command[:200], tool_input={"command": command}, **IDS
    )

    red_edit, _ = redact_event(edit, tool_calls={}, redacted_at="t") or ({}, 0)
    red_bash, bash_removed = redact_event(bash, tool_calls={}, redacted_at="t") or ({}, 0)

    assert red_edit["tool_input"] == {
        "file_path": "/workspace/a.py",
        "old_string": REDACTED_TEXT,
        "new_string": REDACTED_TEXT,
        "replace_all": False,
    }
    assert red_bash["tool_input"] == {"command": REDACTED_TEXT}
    assert red_bash["summary"] == REDACTED_TEXT, "a Bash summary repeats the command, so it must go as well"
    assert set(red_bash["redacted"]["fields"]) == {"command", "summary"}
    assert bash_removed == len(command.encode()) + len(command[:200].encode())


def test_a_call_without_code_is_left_alone() -> None:
    assert redact_event(_read(), tool_calls={}, redacted_at="t") is None, "a Read input is a path, not code"
    grep = events.tool_call(tool_id="g", tool_name="Grep", summary="foo", tool_input={"pattern": "foo"}, **IDS)
    assert redact_event(grep, tool_calls={}, redacted_at="t") is None


def test_every_tool_result_is_code_and_names_its_call() -> None:
    calls = {"t-read": tool_meta(_read())}
    redacted, removed = redact_event(_result("t-read"), tool_calls=calls, redacted_at="t") or ({}, 0)

    assert redacted["content"] == REDACTED_TEXT, "a Read result is the file: it must be replaced"
    assert redacted["is_error"] is False and redacted["tool_id"] == "t-read"
    assert redacted["redacted"]["tool_name"] == "Read"
    assert redacted["redacted"]["file_path"] == "/workspace/app.py"
    assert redacted["redacted"]["fields"]["content"]["sha256"] == _sha(FILE_BODY)
    assert removed == len(FILE_BODY.encode())


def test_redaction_is_idempotent() -> None:
    once, _ = redact_event(_write(), tool_calls={}, redacted_at="t") or ({}, 0)
    assert redact_event(once, tool_calls={}, redacted_at="t2") is None, "a redacted event must not be planned again"


def test_the_stored_marker_is_found_only_on_a_redacted_event() -> None:
    once, _ = redact_event(_write(), tool_calls={}, redacted_at="t") or ({}, 0)
    assert REDACTED_MARKER in json.dumps(once, default=str)
    # The same characters inside a value are escaped in the stored JSON.
    sneaky = events.tool_call(
        tool_id="s", tool_name="Write", summary="p", tool_input={"file_path": "p", "content": REDACTED_MARKER}, **IDS
    )
    assert REDACTED_MARKER not in json.dumps(sneaky, default=str)


def test_plan_job_rewrites_only_events_older_than_the_cutoff() -> None:
    cutoff = NOW - timedelta(days=30)
    rows = [
        LogRow(1, OLD, json.dumps(_read())),
        LogRow(2, OLD, json.dumps(_result("t-read"))),
        LogRow(3, NOW, json.dumps(_result("t-read"))),
        LogRow(4, OLD, "not json"),
    ]
    plan = plan_job(rows, cutoff=cutoff, redacted_at=NOW.isoformat())
    assert [r.id for r in plan] == [2], "only the old result carries code; the newer one must be kept"
    assert json.loads(plan[0].text)["redacted"]["file_path"] == "/workspace/app.py"


def test_the_code_tool_list_is_what_the_docs_name() -> None:
    assert set(CODE_INPUT_FIELDS) == {"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"}


@pytest.mark.asyncio
async def test_unset_means_nothing_is_read_or_written() -> None:
    repo = MagicMock()
    repo.candidate_jobs = AsyncMock()
    repo.rewrite = AsyncMock()
    sweep = JobLogRetentionSweep(repo, retention_days=None)

    result = await sweep.run_once()

    assert not sweep.enabled
    assert (result.jobs, result.events, result.bytes) == (0, 0, 0)
    repo.candidate_jobs.assert_not_called()
    repo.rewrite.assert_not_called()


# --- against Postgres ------------------------------------------------------------


async def _job(db: DatabasePool, status: str) -> str:
    job_id = await JobsRepository(db).create(repo="acme/widgets", task="t")
    await db.execute(
        "UPDATE jobs SET status = $2, total_cost_usd = 1.25, created_at = $3 WHERE job_id = $1",
        job_id,
        status,
        OLD - timedelta(hours=1),
    )
    return job_id


async def _log(db: DatabasePool, job_id: str, event: dict, at: datetime) -> int:
    seq = await JobsRepository(db).append_log_event(job_id, event)
    await db.execute("UPDATE job_logs SET created_at = $2 WHERE id = $1", seq, at)
    return seq


async def _legacy_log(db: DatabasePool, job_id: str, event: dict, at: datetime) -> int:
    """A row written before migration 95: no event_type column value."""
    return int(
        await db.fetchval(
            "INSERT INTO job_logs (job_id, stream, text, created_at) VALUES ($1, 'pipeline', $2, $3) RETURNING id",
            job_id,
            json.dumps(event),
            at,
        )
    )


async def _payload(db: DatabasePool, seq: int) -> dict:
    return json.loads(await db.fetchval("SELECT text FROM job_logs WHERE id = $1", seq))


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_sweep_redacts_only_old_code_and_keeps_the_rest(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    done = await _job(db, "completed")
    live = await _job(db, "running")
    old_write = await _log(db, done, _write(), OLD)
    old_read = await _log(db, done, _read(), OLD)
    old_result = await _log(db, done, _result("t-read"), OLD)
    old_text = await _log(db, done, events.text(text="I will now write app.py", **IDS), OLD)
    new_result = await _log(db, done, _result("t-read"), NOW)
    legacy_result = await _legacy_log(db, done, _result("t-read", "legacy body"), OLD)
    live_result = await _log(db, live, _result("t-x"), OLD)
    before = {
        seq: await db.fetchval("SELECT text FROM job_logs WHERE id = $1", seq)
        for seq in (old_read, old_text, new_result, live_result)
    }

    result = await JobLogRetentionSweep(JobLogRetentionRepository(db), retention_days=30).run_once(now=NOW)

    assert (result.jobs, result.events) == (1, 3), f"expected the old Write, result and legacy result: {result}"
    write = await _payload(db, old_write)
    assert write["tool_input"]["content"] == REDACTED_TEXT and write["tool_input"]["file_path"] == "/workspace/app.py"
    read_result = await _payload(db, old_result)
    assert read_result["content"] == REDACTED_TEXT, "an old Read result still holds the file"
    assert (
        read_result["redacted"]["tool_name"] == "Read" and read_result["redacted"]["file_path"] == "/workspace/app.py"
    )
    assert (await _payload(db, legacy_result))["content"] == REDACTED_TEXT, "a pre-migration-95 row was skipped"
    for seq, text in before.items():
        assert await db.fetchval("SELECT text FROM job_logs WHERE id = $1", seq) == text, (
            f"job_logs row {seq} must not change: a call without code, agent text, a newer event or a live job"
        )
    job = await db.fetchrow("SELECT status, total_cost_usd FROM jobs WHERE job_id = $1", done)
    assert (job["status"], job["total_cost_usd"]) == ("completed", 1.25), "the job row must be left as it was"
    # The API still reads every row as an event.
    payloads = [row["payload"] for row in await JobsRepository(db).get_log_events(done)]
    assert all(isinstance(p, dict) and p.get("type") for p in payloads)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_jobs_logged_before_migration_95_are_found(db_with_migrations: DatabasePool) -> None:
    """Most stored events predate the event_type column; a job holding only those must still be swept."""
    db = db_with_migrations
    calls_only = await _job(db, "completed")
    results_only = await _job(db, "completed")
    legacy_write = await _legacy_log(db, calls_only, _write(), OLD)
    legacy_result = await _legacy_log(db, results_only, _result("t-gone"), OLD)

    result = await JobLogRetentionSweep(JobLogRetentionRepository(db), retention_days=30).run_once(now=NOW)

    assert (result.jobs, result.events) == (2, 2), f"a job with only pre-migration-95 rows was not a candidate: {result}"
    assert (await _payload(db, legacy_write))["tool_input"]["content"] == REDACTED_TEXT
    assert (await _payload(db, legacy_result))["content"] == REDACTED_TEXT


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_second_sweep_changes_nothing(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    done = await _job(db, "failed")
    seq = await _log(db, done, _write(), OLD)
    sweep = JobLogRetentionSweep(JobLogRetentionRepository(db), retention_days=30)
    await sweep.run_once(now=NOW)
    first = await db.fetchval("SELECT text FROM job_logs WHERE id = $1", seq)

    again = await sweep.run_once(now=NOW + timedelta(days=1))

    assert (again.jobs, again.events, again.bytes) == (0, 0, 0), "a redacted job came back as a candidate"
    assert await db.fetchval("SELECT text FROM job_logs WHERE id = $1", seq) == first


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_dry_run_counts_what_the_sweep_removes_and_writes_nothing(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    done = await _job(db, "pr_merged")
    seqs = [await _log(db, done, _write(), OLD), await _log(db, done, _result("t-write", "ok"), OLD)]
    texts = [await db.fetchval("SELECT text FROM job_logs WHERE id = $1", s) for s in seqs]
    sweep = JobLogRetentionSweep(JobLogRetentionRepository(db), retention_days=30, batch_jobs=1)

    preview = await sweep.run_once(dry_run=True, now=NOW)

    assert [await db.fetchval("SELECT text FROM job_logs WHERE id = $1", s) for s in seqs] == texts
    real = await sweep.run_once(now=NOW)
    assert preview.as_dict() == real.as_dict(), "the dry run must report exactly what the sweep then does"
    assert real.bytes == len(FILE_BODY.encode()) + len(b"ok")


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_unset_leaves_old_code_in_place(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    done = await _job(db, "completed")
    seq = await _log(db, done, _write(), OLD)
    text = await db.fetchval("SELECT text FROM job_logs WHERE id = $1", seq)

    await JobLogRetentionSweep(JobLogRetentionRepository(db), retention_days=None).run_once(now=NOW)

    assert await db.fetchval("SELECT text FROM job_logs WHERE id = $1", seq) == text
