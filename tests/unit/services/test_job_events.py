"""The one path from a producer to job_logs and the bus, and the job_finished emission (spec 3.5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from prometheus_client import CollectorRegistry

from embry0.api.events.bus import EventBus
from embry0.metrics import LoopMetrics
from embry0.safety.redaction import REDACTED, register_known_secrets
from embry0.services.job_events import broadcast_job_event, emit_job_finished, reset_sequence_memory


def _jobs(*, seq: int | None = 7, row: dict | None = None, persist_error: Exception | None = None) -> MagicMock:
    jobs = MagicMock()
    if persist_error is not None:
        jobs.append_log_event = AsyncMock(side_effect=persist_error)
    else:
        jobs.append_log_event = AsyncMock(return_value=seq)
    jobs.increment_cost = AsyncMock()
    jobs.get = AsyncMock(return_value=row)
    return jobs


@pytest.fixture(autouse=True)
def _forget_sequences():
    reset_sequence_memory()
    yield
    reset_sequence_memory()


@pytest.mark.asyncio
async def test_broadcast_stamps_job_id_timestamp_and_seq_and_publishes() -> None:
    jobs = _jobs(seq=42)
    bus = EventBus()
    queue = await bus.subscribe("job-1")
    out = await broadcast_job_event(jobs, bus, None, "job-1", {"type": "progress", "message": "hi"})
    assert out is not None and out["event_seq"] == 42 and out["job_id"] == "job-1" and out["timestamp"]
    assert queue.get_nowait() == out
    persisted = jobs.append_log_event.await_args.args[1]
    assert persisted["job_id"] == "job-1" and "event_seq" not in persisted, (
        "the seq comes from the row id, not the payload"
    )


@pytest.mark.asyncio
async def test_broadcast_without_a_bus_only_persists() -> None:
    jobs = _jobs(seq=1)
    out = await broadcast_job_event(jobs, None, None, "job-1", {"type": "system", "message": "boot"})
    assert out is not None and out["event_seq"] == 1
    jobs.append_log_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_persist_failure_counts_and_still_publishes_without_a_seq() -> None:
    registry = CollectorRegistry()
    metrics = LoopMetrics(registry)
    jobs = _jobs(persist_error=RuntimeError("db down"))
    bus = EventBus()
    queue = await bus.subscribe("job-1")
    out = await broadcast_job_event(jobs, bus, metrics, "job-1", {"type": "progress", "message": "hi"})
    assert out is not None and "event_seq" not in out
    assert queue.get_nowait()["type"] == "progress"
    assert registry.get_sample_value("embry0_job_events_persist_failed_total") == 1, (
        "a failed persist must be visible in Prometheus (spec 3.5)"
    )


@pytest.mark.asyncio
async def test_cost_update_increments_the_job_total_once() -> None:
    jobs = _jobs()
    await broadcast_job_event(jobs, None, None, "job-1", {"type": "cost_update", "cost_usd": 0.25})
    await broadcast_job_event(jobs, None, None, "job-1", {"type": "turn_usage", "cost_usd": 0.10})
    assert [c.args for c in jobs.increment_cost.await_args_list] == [("job-1", 0.25)], (
        "only cost_update adds to jobs.total_cost_usd (spec 3.3)"
    )


@pytest.mark.asyncio
async def test_emit_job_finished_reads_the_row_and_publishes_the_terminal_event() -> None:
    row = {
        "job_id": "job-1",
        "status": "failed",
        "error_code": "ERR_AGENT_TIMEOUT",
        "error_message": "the agent timed out",
        "pr_url": None,
        "total_cost_usd": 1.25,
    }
    jobs = _jobs(seq=9, row=row)
    bus = EventBus()
    queue = await bus.subscribe("job-1")
    await emit_job_finished(jobs, bus, None, "job-1")
    event = queue.get_nowait()
    assert event["type"] == "job_finished", "a terminal transition must emit job_finished (spec 3.5)"
    assert event["status"] == "failed" and event["error_code"] == "ERR_AGENT_TIMEOUT"
    assert event["error_message"] == "the agent timed out" and event["pr_url"] is None
    assert event["total_cost_usd"] == 1.25 and event["event_seq"] == 9


@pytest.mark.asyncio
async def test_emit_job_finished_skips_a_row_that_is_not_terminal() -> None:
    jobs = _jobs(row={"job_id": "job-1", "status": "running"})
    bus = EventBus()
    queue = await bus.subscribe("job-1")
    await emit_job_finished(jobs, bus, None, "job-1")
    assert queue.qsize() == 0, "job_finished must never describe a job that is still running"
    jobs.append_log_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_emit_job_finished_never_raises() -> None:
    jobs = _jobs()
    jobs.get = AsyncMock(side_effect=RuntimeError("db down"))
    await emit_job_finished(jobs, None, None, "job-1")  # must not raise
    jobs.append_log_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_executor_broadcast_delegates_and_announce_finished_uses_its_repos() -> None:
    from embry0.services.issue_executor import IssueExecutor

    jobs = _jobs(
        seq=3,
        row={
            "job_id": "job-1",
            "status": "completed",
            "error_code": None,
            "error_message": None,
            "pr_url": "https://example.invalid/pr/1",
            "total_cost_usd": 0.5,
        },
    )
    bus = EventBus()
    queue = await bus.subscribe("job-1")
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = jobs
    ex._event_bus = bus
    await ex._announce_finished("job-1")
    event = queue.get_nowait()
    assert event["type"] == "job_finished" and event["pr_url"] == "https://example.invalid/pr/1"
    assert event["event_seq"] == 3


def test_executor_constructor_stores_metrics() -> None:
    from embry0.services.issue_executor import IssueExecutor

    metrics = LoopMetrics(CollectorRegistry())
    ex = IssueExecutor(
        issues_repo=MagicMock(),
        jobs_repo=MagicMock(),
        traces_repo=MagicMock(),
        workflow_registry=MagicMock(),
        database_url="postgres://stub",
        metrics=metrics,
    )
    assert ex._metrics is metrics, "IssueExecutor(metrics=...) must store the facade as _metrics"


# ---- spec 3.6 / 3.9: redaction in the core ----------------------------------


@pytest.fixture(autouse=True)
def _clean_known_secrets():
    register_known_secrets([], reset=True)
    yield
    register_known_secrets([], reset=True)


@pytest.mark.asyncio
async def test_broadcast_redacts_nested_values_before_persist_and_publish() -> None:
    """Review focus 3: a registered secret two levels down and one in a list block."""
    secret = "registered-secret-value-00112233"
    register_known_secrets([secret])
    jobs = _jobs(seq=1)
    bus = EventBus()
    queue = await bus.subscribe("job-1")
    event = {
        "type": "tool_call",
        "tool_id": "t1",
        "tool_name": "Bash",
        "summary": "run",
        "tool_input": {"command": "run", "env": [{"name": "K", "value": secret}]},
        "run_id": "run-1",
    }
    await broadcast_job_event(jobs, bus, None, "job-1", event)
    persisted = jobs.append_log_event.await_args.args[1]
    published = queue.get_nowait()
    assert persisted["tool_input"]["env"][0]["value"] == REDACTED, "scrubbed before persist"
    assert published["tool_input"]["env"][0]["value"] == REDACTED, "scrubbed before publish"
    assert persisted["tool_id"] == "t1" and persisted["run_id"] == "run-1"
    assert event["tool_input"]["env"][0]["value"] == secret, "the caller's dict is not mutated"

    content_event = {"type": "tool_result", "tool_id": "t1", "is_error": False, "content": f"line {secret}"}
    await broadcast_job_event(jobs, bus, None, "job-1", content_event)
    assert secret not in jobs.append_log_event.await_args.args[1]["content"]


@pytest.mark.asyncio
async def test_a_scrub_failure_drops_the_event_and_logs_only_its_type(monkeypatch, capsys) -> None:
    """spec 3.9: leaking is worse than a missing line."""
    import embry0.services.job_events as job_events

    def _boom(event, values):
        raise RuntimeError("regex exploded")

    monkeypatch.setattr(job_events, "redact_event", _boom)
    jobs = _jobs(seq=1)
    bus = EventBus()
    queue = await bus.subscribe("job-1")
    out = await broadcast_job_event(jobs, bus, None, "job-1", {"type": "text", "text": "the payload"})
    assert out is None
    jobs.append_log_event.assert_not_awaited()
    assert queue.qsize() == 0, "a line that could not be scrubbed must not reach anyone"
    logged = capsys.readouterr().out
    assert "event_redaction_failed" in logged and "text" in logged
    assert "the payload" not in logged, "the log line carries the event type only"


# ---- spec 3.5: prev_seq, so a client can tell a gap from an interleave --------


def _seq_jobs(*seqs: int | Exception) -> MagicMock:
    """append_log_event answers each call from ``seqs`` in order (an Exception is raised)."""
    jobs = MagicMock()
    jobs.append_log_event = AsyncMock(side_effect=list(seqs))
    jobs.increment_cost = AsyncMock()
    return jobs


async def _published(jobs: MagicMock, job_id: str, event: dict) -> dict:
    bus = EventBus()
    queue = await bus.subscribe(job_id)
    out = await broadcast_job_event(jobs, bus, None, job_id, event)
    assert out is not None
    assert queue.get_nowait() == out
    return out


@pytest.mark.asyncio
async def test_the_second_event_of_a_job_carries_the_first_ones_seq_as_prev_seq() -> None:
    jobs = _seq_jobs(100, 107)
    first = await _published(jobs, "job-1", {"type": "progress", "message": "a"})
    second = await _published(jobs, "job-1", {"type": "progress", "message": "b"})
    assert "prev_seq" not in first, "a job's first event has no previous persisted seq"
    assert second.get("prev_seq") == 100, (
        f"prev_seq must be the job's previous persisted event_seq (100), got {second.get('prev_seq')!r}"
    )
    persisted = jobs.append_log_event.await_args.args[1]
    assert persisted.get("prev_seq") == 100, "prev_seq is stamped before the persist, so a replay carries it too"


@pytest.mark.asyncio
async def test_a_failed_persist_does_not_advance_prev_seq() -> None:
    jobs = _seq_jobs(100, RuntimeError("db down"), 109)
    await _published(jobs, "job-1", {"type": "progress", "message": "a"})
    lost = await _published(jobs, "job-1", {"type": "progress", "message": "b"})
    assert lost.get("prev_seq") == 100 and "event_seq" not in lost
    third = await _published(jobs, "job-1", {"type": "progress", "message": "c"})
    assert third.get("prev_seq") == 100, (
        f"an event that was never persisted cannot be the previous persisted one; got {third.get('prev_seq')!r}"
    )


@pytest.mark.asyncio
async def test_job_finished_forgets_the_job() -> None:
    import embry0.services.job_events as job_events

    jobs = _seq_jobs(100, 101, 102)
    await _published(jobs, "job-1", {"type": "progress", "message": "a"})
    finished = await _published(
        jobs,
        "job-1",
        {
            "type": "job_finished",
            "status": "completed",
            "error_code": None,
            "error_message": None,
            "pr_url": None,
            "total_cost_usd": 0.0,
        },
    )
    assert finished.get("prev_seq") == 100, "the terminal event itself still points at its predecessor"
    assert "job-1" not in job_events._LAST_SEQ, "the map is bounded by live jobs: job_finished must drop the entry"
    late = await _published(jobs, "job-1", {"type": "job_failed", "kind": "late", "message": "x"})
    assert "prev_seq" not in late


@pytest.mark.asyncio
async def test_another_jobs_events_do_not_move_this_jobs_prev_seq() -> None:
    jobs = _seq_jobs(100, 101, 102, 103)
    await _published(jobs, "job-1", {"type": "progress", "message": "a"})
    await _published(jobs, "job-2", {"type": "progress", "message": "b"})
    await _published(jobs, "job-2", {"type": "progress", "message": "c"})
    out = await _published(jobs, "job-1", {"type": "progress", "message": "d"})
    assert out.get("prev_seq") == 100, (
        f"job-1's previous persisted seq is 100 whatever job-2 wrote in between; got {out.get('prev_seq')!r}"
    )
