"""In-process pub/sub for QA state changes.

Single-process only (per LAN-only deploy). Subscribers receive only
events published while their iterator is active; no replay buffer.
Late-joining clients catch up via the polling fallback in the dashboard.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any


class QAEventBus:
    """Per-run-id pub/sub backed by ``asyncio.Queue`` per subscriber.

    The bus is intentionally tiny: each ``subscribe`` call returns an async
    iterator that yields events published *to that run_id while the iterator
    is alive*. There's no replay buffer; clients that connect after an event
    has been published miss it and rely on the dashboard's 15s polling
    fallback to reconcile.

    Thread-safety: all calls must run on the same event loop. embry0's API
    process is single-threaded for routes / lifespan, so this matches the
    deployment topology.
    """

    def __init__(self, queue_maxsize: int = 64) -> None:
        # ``defaultdict(list)`` is convenient for ``subscribe`` but it auto-
        # creates an entry on every read — we keep that and prune empty
        # lists in the subscribe-cleanup path so ``_channels`` doesn't grow
        # unbounded for every published-to run_id.
        self._channels: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
        self._queue_maxsize = queue_maxsize

    async def publish(self, run_id: str, event: dict[str, Any]) -> None:
        """Fan an event out to every subscriber on ``run_id``.

        If a subscriber's queue is full, the *oldest* event in that queue is
        dropped to make room — the publisher never blocks on a slow consumer.
        Drops are silent because the dashboard's 15s polling reconciles state
        regardless.
        """
        # Snapshot the subscriber list — a subscriber's iterator finally-block
        # may mutate the underlying list during iteration if it gets cancelled
        # while we're publishing.
        for q in list(self._channels.get(run_id, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest event from this slow subscriber's queue
                # rather than blocking the publisher. The polling fallback
                # will reconcile any drops on the next tick.
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    # Still full — another coroutine raced us. Give up
                    # rather than loop; polling will reconcile.
                    pass

    async def subscribe(self, run_id: str) -> AsyncIterator[dict[str, Any]]:
        """Async-iterate events for ``run_id`` until the iterator is closed.

        Usage::

            async for event in bus.subscribe(run_id):
                ...

        The iterator is unbounded by design — callers must ``break`` (or let
        the generator be garbage-collected) to stop receiving events. The SSE
        route closes the iterator when the client disconnects or a ``done``
        event is seen.
        """
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_maxsize)
        self._channels[run_id].append(q)
        try:
            while True:
                yield await q.get()
        finally:
            try:
                self._channels[run_id].remove(q)
            except ValueError:
                pass
            if not self._channels[run_id]:
                # Prune the empty list so a dashboard that hits run_ids in a
                # long-running process doesn't leak channel keys forever.
                del self._channels[run_id]
