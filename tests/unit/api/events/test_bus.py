"""EventBus concurrency + semantics tests."""

import asyncio

import pytest


@pytest.mark.asyncio
async def test_event_bus_subscribe_returns_queue():
    from legion.api.events.bus import EventBus

    bus = EventBus()
    q = await bus.subscribe("job-1")
    assert isinstance(q, asyncio.Queue)


@pytest.mark.asyncio
async def test_event_bus_publish_delivers_to_subscribers():
    from legion.api.events.bus import EventBus

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
    from legion.api.events.bus import EventBus

    bus = EventBus()
    await bus.publish("no-subscribers", {"type": "progress"})


@pytest.mark.asyncio
async def test_event_bus_unsubscribe_removes_queue():
    from legion.api.events.bus import EventBus

    bus = EventBus()
    q = await bus.subscribe("job-1")
    await bus.unsubscribe("job-1", q)

    await bus.publish("job-1", {"type": "progress"})
    assert q.qsize() == 0


@pytest.mark.asyncio
async def test_event_bus_unsubscribe_cleans_up_empty_lists():
    """After the last subscriber leaves, the job_id entry is removed."""
    from legion.api.events.bus import EventBus

    bus = EventBus()
    q = await bus.subscribe("job-1")
    await bus.unsubscribe("job-1", q)
    assert "job-1" not in bus._subscribers  # noqa: SLF001


@pytest.mark.asyncio
async def test_event_bus_publish_does_not_block_on_failing_subscriber(capsys):
    """A subscriber whose put_nowait raises must not stop delivery to others,
    and the failure must be logged (not silently swallowed)."""
    from legion.api.events.bus import EventBus

    bus = EventBus()
    q_good = await bus.subscribe("job-1")
    q_bad = await bus.subscribe("job-1")

    def _raise(*args, **kwargs):
        raise asyncio.QueueFull()

    q_bad.put_nowait = _raise  # type: ignore[method-assign]

    await bus.publish("job-1", {"type": "progress", "message": "x"})

    assert q_good.qsize() == 1
    captured = capsys.readouterr()
    assert "subscriber_put_failed" in captured.out


@pytest.mark.asyncio
async def test_event_bus_concurrent_subscribe_and_publish_no_race():
    """Stress: 50 concurrent subscribes + 50 concurrent publishes must not
    raise KeyError, list-mutation-during-iteration, or drop messages."""
    from legion.api.events.bus import EventBus

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
    from legion.api.events.bus import EventBus

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
