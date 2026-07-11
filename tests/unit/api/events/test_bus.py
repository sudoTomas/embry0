"""EventBus concurrency + semantics tests."""

import asyncio

import pytest


@pytest.mark.asyncio
async def test_event_bus_subscribe_returns_queue():
    from embry0.api.events.bus import EventBus

    bus = EventBus()
    q = await bus.subscribe("job-1")
    assert isinstance(q, asyncio.Queue)


@pytest.mark.asyncio
async def test_event_bus_publish_delivers_to_subscribers():
    from embry0.api.events.bus import EventBus

    bus = EventBus()
    q1 = await bus.subscribe("job-1")
    q2 = await bus.subscribe("job-1")

    await bus.publish("job-1", {"type": "progress", "message": "hi"})

    assert q1.qsize() == 1
    assert q2.qsize() == 1
    evt1 = q1.get_nowait()
    assert evt1["type"] == "progress"


@pytest.mark.asyncio
async def test_event_bus_publish_ignores_unknown_job():
    """Publishing for a job with no subscribers is a no-op — no error."""
    from embry0.api.events.bus import EventBus

    bus = EventBus()
    await bus.publish("no-subscribers", {"type": "progress"})


@pytest.mark.asyncio
async def test_event_bus_unsubscribe_removes_queue():
    from embry0.api.events.bus import EventBus

    bus = EventBus()
    q = await bus.subscribe("job-1")
    await bus.unsubscribe("job-1", q)

    await bus.publish("job-1", {"type": "progress"})
    assert q.qsize() == 0


@pytest.mark.asyncio
async def test_event_bus_unsubscribe_cleans_up_empty_lists():
    """After the last subscriber leaves, the job_id entry is removed."""
    from embry0.api.events.bus import EventBus

    bus = EventBus()
    q = await bus.subscribe("job-1")
    await bus.unsubscribe("job-1", q)
    assert "job-1" not in bus._subscribers  # noqa: SLF001


@pytest.mark.asyncio
async def test_event_bus_publish_does_not_block_on_failing_subscriber(capsys):
    """A subscriber whose put_nowait raises must not stop delivery to others,
    and the failure must be logged (not silently swallowed).

    QueueFull is now caught as ws_slow_consumer; other exceptions go to
    subscriber_put_failed. Both keep delivery going for remaining subscribers.
    """
    import structlog.testing

    from embry0.api.events.bus import EventBus

    bus = EventBus()
    q_good = await bus.subscribe("job-1")
    q_bad = await bus.subscribe("job-1")

    # Simulate a non-QueueFull error (e.g. closed queue) → subscriber_put_failed
    class _SpecialError(Exception):
        pass

    def _raise(*args, **kwargs):
        raise _SpecialError("boom")

    q_bad.put_nowait = _raise  # type: ignore[method-assign]

    with structlog.testing.capture_logs() as cap:
        await bus.publish("job-1", {"type": "progress", "message": "x"})

    assert q_good.qsize() == 1
    failed_events = [e for e in cap if e.get("event") == "subscriber_put_failed"]
    assert len(failed_events) == 1
    assert "boom" in failed_events[0]["error"]


@pytest.mark.asyncio
async def test_event_bus_concurrent_subscribe_and_publish_no_race():
    """Stress: 50 concurrent subscribes + 50 concurrent publishes must not
    raise KeyError, list-mutation-during-iteration, or drop messages."""
    from embry0.api.events.bus import EventBus

    bus = EventBus()
    queues: list[asyncio.Queue] = []

    async def _subscribe_many() -> None:
        for _ in range(50):
            q = await bus.subscribe("job-1")
            queues.append(q)
            await asyncio.sleep(0)

    async def _publish_many() -> None:
        for i in range(50):
            await bus.publish("job-1", {"type": "progress", "n": i})
            await asyncio.sleep(0)

    await asyncio.gather(_subscribe_many(), _publish_many())

    total_delivered = sum(q.qsize() for q in queues)
    assert total_delivered > 0


@pytest.mark.asyncio
async def test_event_bus_concurrent_unsubscribe_during_publish():
    """Unsubscribing concurrently with publish must not crash the publisher."""
    from embry0.api.events.bus import EventBus

    bus = EventBus()
    q = await bus.subscribe("job-1")

    async def _unsubscribe_soon() -> None:
        await asyncio.sleep(0)
        await bus.unsubscribe("job-1", q)

    async def _publish_many() -> None:
        for i in range(100):
            await bus.publish("job-1", {"type": "progress", "n": i})
            await asyncio.sleep(0)

    await asyncio.gather(_unsubscribe_soon(), _publish_many())


@pytest.mark.asyncio
async def test_event_bus_bounded_queue_drops_on_full():
    """A full subscriber queue triggers ws_slow_consumer warning; does not raise."""
    import structlog.testing

    from embry0.api.events.bus import EventBus

    bus = EventBus()
    q = await bus.subscribe("job-1", maxsize=2)

    with structlog.testing.capture_logs() as cap:
        await bus.publish("job-1", {"type": "a"})
        await bus.publish("job-1", {"type": "b"})
        # Queue is now full (maxsize=2)
        await bus.publish("job-1", {"type": "c"})  # must drop, not raise

    # The dropped event must not have been enqueued
    assert q.qsize() == 2

    # The ws_slow_consumer warning must have been emitted
    warning_events = [e for e in cap if e.get("log_level") == "warning" and e.get("event") == "ws_slow_consumer"]
    assert len(warning_events) == 1
    assert warning_events[0]["job_id"] == "job-1"


@pytest.mark.asyncio
async def test_event_bus_subscribe_default_maxsize():
    """Default subscribe creates a bounded queue (maxsize=1000)."""
    from embry0.api.events.bus import EventBus

    bus = EventBus()
    q = await bus.subscribe("job-1")
    assert q.maxsize == 1000
