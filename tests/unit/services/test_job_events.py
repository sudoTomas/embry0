"""The one path from a producer to job_logs and the bus, and the job_finished emission (spec 3.5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from prometheus_client import CollectorRegistry

from embry0.api.events.bus import EventBus
from embry0.metrics import LoopMetrics
from embry0.services.job_events import broadcast_job_event, emit_job_finished


def _jobs(*, seq: int | None = 7, row: dict | None = None, persist_error: Exception | None = None) -> MagicMock:
    jobs = MagicMock()
    if persist_error is not None:
        jobs.append_log_event = AsyncMock(side_effect=persist_error)
    else:
        jobs.append_log_event = AsyncMock(return_value=seq)
    jobs.increment_cost = AsyncMock()
    jobs.get = AsyncMock(return_value=row)
    return jobs


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
