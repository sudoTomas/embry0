"""Concurrency-safe event bus for per-job WebSocket delivery.

Encapsulates the subscriber dict so all mutation happens under an asyncio.Lock.
Callers are the WS streaming endpoint (subscribe/unsubscribe) and IssueExecutor
(publish). See docs/superpowers/plans/2026-04-14-plan-b1-event-streaming-hardening.md.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class EventBus:
    """Per-job asyncio.Queue fan-out with lock-protected subscriber registry.

    One ``EventBus`` instance lives at ``app.state.event_bus``.

    Callers:
    - ``IssueExecutor._broadcast_event`` → ``publish``
    - WS ``/ws/jobs/{job_id}/events`` → ``subscribe`` / ``unsubscribe``
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[Any]]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, job_id: str, maxsize: int = 1000) -> asyncio.Queue[Any]:
        """Register a new subscriber for ``job_id``; return its queue.

        Queues are bounded at ``maxsize`` events; on overflow, ``publish``
        drops with a ``ws_slow_consumer`` warning rather than blocking.
        """
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)
        async with self._lock:
            self._subscribers.setdefault(job_id, []).append(queue)
        return queue

    async def unsubscribe(self, job_id: str, queue: asyncio.Queue[Any]) -> None:
        """Remove ``queue`` from the subscriber list for ``job_id``.

        Cleans up the dict entry if the list becomes empty. Silent no-op if
        the job_id or queue is unknown (idempotent).
        """
        async with self._lock:
            subs = self._subscribers.get(job_id)
            if not subs:
                return
            try:
                subs.remove(queue)
            except ValueError:
                return
            if not subs:
                self._subscribers.pop(job_id, None)

    async def publish(self, job_id: str, event: dict[str, Any]) -> None:
        """Deliver ``event`` to every subscriber of ``job_id``.

        A failing subscriber (e.g. full queue, closed connection) does NOT
        stop delivery to the others; the failure is logged with enough
        context for diagnosis.
        """
        async with self._lock:
            # Snapshot the subscriber list while holding the lock so we
            # iterate a stable list even if an unsubscribe lands concurrently.
            subscribers = list(self._subscribers.get(job_id, []))

        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "ws_slow_consumer",
                    job_id=job_id,
                    event_type=event.get("type", "unknown"),
                    queue_size=queue.qsize(),
                )
            except Exception as exc:
                logger.warning(
                    "subscriber_put_failed",
                    job_id=job_id,
                    event_type=event.get("type", "unknown"),
                    error=str(exc),
                )
