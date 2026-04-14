"""Unit tests for IssueExecutor event broadcasting and cost accumulation."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_broadcast_event_accumulates_cost_across_events():
    """Successive cost_update events must ADD, not overwrite."""
    from legion.services.issue_executor import IssueExecutor

    jobs = MagicMock()
    jobs.append_log_event = AsyncMock()
    jobs.increment_cost = AsyncMock()
    jobs.update = AsyncMock()

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._jobs = jobs
    executor._event_subscribers = {}

    await executor._broadcast_event("job-1", {"type": "cost_update", "cost_usd": 0.10})
    await executor._broadcast_event("job-1", {"type": "cost_update", "cost_usd": 0.05})
    await executor._broadcast_event("job-1", {"type": "cost_update", "cost_usd": 0.20})

    assert jobs.increment_cost.await_count == 3
    calls = [call.args for call in jobs.increment_cost.await_args_list]
    assert calls == [("job-1", 0.10), ("job-1", 0.05), ("job-1", 0.20)]

    update_calls_for_cost = [c for c in jobs.update.await_args_list if "total_cost_usd" in c.kwargs]
    assert update_calls_for_cost == [], f"update() must not be used for cost accumulation: {update_calls_for_cost}"


@pytest.mark.asyncio
async def test_broadcast_event_ignores_cost_update_without_amount():
    """cost_update events with missing or zero cost_usd must not call increment_cost."""
    from legion.services.issue_executor import IssueExecutor

    jobs = MagicMock()
    jobs.append_log_event = AsyncMock()
    jobs.increment_cost = AsyncMock()

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._jobs = jobs
    executor._event_subscribers = {}

    await executor._broadcast_event("job-1", {"type": "cost_update"})
    await executor._broadcast_event("job-1", {"type": "cost_update", "cost_usd": 0})
    await executor._broadcast_event("job-1", {"type": "something_else", "cost_usd": 0.99})

    assert jobs.increment_cost.await_count == 0


@pytest.mark.asyncio
async def test_broadcast_event_logs_subscriber_exception(capsys):
    """Failing subscribers must be logged, not silently swallowed.

    Uses capsys because structlog is not configured to wrap stdlib logging in
    this project — its default PrintLogger writes directly to stdout, so the
    stdlib-based caplog fixture would not see the record.
    """
    import asyncio

    from legion.services.issue_executor import IssueExecutor

    jobs = MagicMock()
    jobs.append_log_event = AsyncMock()
    jobs.increment_cost = AsyncMock()

    # Subscriber whose put_nowait always raises
    bad_queue = MagicMock()
    bad_queue.put_nowait = MagicMock(side_effect=asyncio.QueueFull())

    good_queue = MagicMock()
    good_queue.put_nowait = MagicMock()

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._jobs = jobs
    executor._event_subscribers = {"job-1": [bad_queue, good_queue]}

    await executor._broadcast_event("job-1", {"type": "progress", "message": "hi"})

    # Good subscriber still received the event (one bad subscriber doesn't block others)
    assert good_queue.put_nowait.call_count == 1

    # Bad subscriber failure was logged to stdout (structlog default PrintLogger)
    captured = capsys.readouterr()
    assert "subscriber_put_failed" in captured.out, (
        f"Expected a 'subscriber_put_failed' log line. Got stdout: {captured.out!r}"
    )
    # And the log carries diagnostic context
    assert "job-1" in captured.out
    assert "progress" in captured.out
