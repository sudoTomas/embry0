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
    from legion.api.events.bus import EventBus

    executor._event_bus = EventBus()

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
    from legion.api.events.bus import EventBus

    executor._event_bus = EventBus()

    await executor._broadcast_event("job-1", {"type": "cost_update"})
    await executor._broadcast_event("job-1", {"type": "cost_update", "cost_usd": 0})
    await executor._broadcast_event("job-1", {"type": "something_else", "cost_usd": 0.99})

    assert jobs.increment_cost.await_count == 0


@pytest.mark.asyncio
async def test_broadcast_event_stamps_event_seq_from_persist():
    """The seq returned by append_log_event must be stamped onto the event
    before it reaches subscribers, so WS clients can use it as a cursor."""
    from legion.api.events.bus import EventBus
    from legion.services.issue_executor import IssueExecutor

    jobs = MagicMock()
    jobs.append_log_event = AsyncMock(return_value=12345)
    jobs.increment_cost = AsyncMock()

    bus = EventBus()
    queue = await bus.subscribe("job-1")

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._jobs = jobs
    executor._event_bus = bus

    await executor._broadcast_event("job-1", {"type": "progress", "message": "hi"})

    delivered = queue.get_nowait()
    assert delivered["event_seq"] == 12345
    assert delivered["type"] == "progress"


@pytest.mark.asyncio
async def test_broadcast_event_no_event_seq_when_persist_fails():
    """If append_log_event raises, the event still reaches subscribers but
    carries no event_seq stamp (nothing to stamp since nothing was persisted)."""
    from legion.api.events.bus import EventBus
    from legion.services.issue_executor import IssueExecutor

    jobs = MagicMock()
    jobs.append_log_event = AsyncMock(side_effect=RuntimeError("db down"))
    jobs.increment_cost = AsyncMock()

    bus = EventBus()
    queue = await bus.subscribe("job-1")

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._jobs = jobs
    executor._event_bus = bus

    await executor._broadcast_event("job-1", {"type": "progress", "message": "hi"})

    delivered = queue.get_nowait()
    assert "event_seq" not in delivered
    assert delivered["type"] == "progress"


