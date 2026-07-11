"""Unit tests for embry0.qa.event_bus.QAEventBus.

Coverage:
  1. Subscriber receives an event published after subscribe().
  2. publish() with no subscribers does not raise.
  3. Multiple subscribers on the same run_id each receive one publish.
  4. Subscribers on a different run_id do NOT receive the event.
  5. A full queue drops the oldest event (not the new one) and does
     not block the publisher.
"""

from __future__ import annotations

import asyncio

import pytest

from embry0.qa.event_bus import QAEventBus

# ---------------------------------------------------------------------------
# Helper: drain a subscriber until it sees a sentinel "done" event.
# ---------------------------------------------------------------------------


async def _collect_until_done(bus: QAEventBus, run_id: str) -> list[dict]:
    """Subscribe to ``run_id`` and collect events until a ``done`` sentinel.

    The bus has no built-in stop signal; production code uses ``type='done'``
    as the stream terminator. The tests reuse that convention so each
    subscribe-iterator finishes deterministically without relying on
    cancellation timeouts.
    """
    received: list[dict] = []
    async for event in bus.subscribe(run_id):
        received.append(event)
        if event.get("type") == "done":
            return received
    return received


# ---------------------------------------------------------------------------
# Test 1: subscriber receives a published event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscriber_receives_published_event() -> None:
    bus = QAEventBus()

    task = asyncio.create_task(_collect_until_done(bus, "run-1"))

    # Yield once so the subscriber registers its queue before publish().
    await asyncio.sleep(0)

    await bus.publish("run-1", {"type": "subtask_status", "app": "hub"})
    await bus.publish("run-1", {"type": "done"})

    received = await asyncio.wait_for(task, timeout=1.0)
    assert received == [
        {"type": "subtask_status", "app": "hub"},
        {"type": "done"},
    ]


# ---------------------------------------------------------------------------
# Test 2: publish with no subscribers does not raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_with_no_subscribers_does_not_raise() -> None:
    bus = QAEventBus()
    # Must not raise even though nobody is listening for "ghost-run".
    await bus.publish("ghost-run", {"type": "subtask_status"})
    # Internal channel dict should not have grown for the ghost run_id.
    assert "ghost-run" not in bus._channels


# ---------------------------------------------------------------------------
# Test 3: multiple subscribers on the same run_id each receive one publish
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_subscribers_each_receive_event() -> None:
    bus = QAEventBus()

    t1 = asyncio.create_task(_collect_until_done(bus, "run-A"))
    t2 = asyncio.create_task(_collect_until_done(bus, "run-A"))

    # Both subscribers must register before we publish.
    await asyncio.sleep(0)

    await bus.publish("run-A", {"type": "subtask_status", "app": "hub"})
    await bus.publish("run-A", {"type": "done"})

    received_1 = await asyncio.wait_for(t1, timeout=1.0)
    received_2 = await asyncio.wait_for(t2, timeout=1.0)

    assert received_1 == received_2
    assert received_1[0]["app"] == "hub"
    assert received_1[-1]["type"] == "done"


# ---------------------------------------------------------------------------
# Test 4: subscribers on a different run_id do not receive the event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscriber_on_other_run_id_does_not_receive() -> None:
    bus = QAEventBus()

    other = asyncio.create_task(_collect_until_done(bus, "run-B"))
    await asyncio.sleep(0)

    # Publish on run-A; the run-B subscriber should not see anything yet.
    await bus.publish("run-A", {"type": "subtask_status"})

    # The run-B subscriber is still waiting — terminate it ourselves.
    await bus.publish("run-B", {"type": "done"})

    received = await asyncio.wait_for(other, timeout=1.0)
    # Only the explicitly-targeted "done" event should have arrived.
    assert received == [{"type": "done"}]


# ---------------------------------------------------------------------------
# Test 5: slow subscriber drops oldest event when queue full;
#         publisher does not block.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slow_subscriber_drops_oldest_event_when_queue_full() -> None:
    """Publisher never blocks on a slow consumer; oldest event is dropped."""
    bus = QAEventBus(queue_maxsize=3)

    # Manually attach a queue (mirrors what subscribe() does internally) so
    # we can inspect drop behavior without consuming from the queue.
    q: asyncio.Queue[dict] = asyncio.Queue(maxsize=3)
    bus._channels["run-slow"].append(q)

    # Fill the queue.
    await bus.publish("run-slow", {"n": 1})
    await bus.publish("run-slow", {"n": 2})
    await bus.publish("run-slow", {"n": 3})
    assert q.qsize() == 3

    # Publish one more — should drop {"n": 1} and enqueue {"n": 4}, all
    # without raising or blocking the publisher.
    await asyncio.wait_for(bus.publish("run-slow", {"n": 4}), timeout=0.5)

    items: list[dict] = []
    while not q.empty():
        items.append(q.get_nowait())

    assert items == [{"n": 2}, {"n": 3}, {"n": 4}]
