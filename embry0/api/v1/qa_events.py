"""SSE liveness stream for /api/v1/qa/runs/{run_id}/events.

Phase 5C of the QA dashboard pending-frontend plan: the dashboard polls
``GET /api/v1/qa/runs/{run_id}`` every 15 s today; this route lets the
frontend open an EventSource so per-app status changes propagate within a
heartbeat instead of after the next poll. Polling stays as the fallback —
when an EventSource disconnects (auth, network, server restart) the 15 s
poller in ``useQaRun`` keeps the dashboard live.

Auth: this router is registered with the same ``require_auth`` dependency as
``qa_dashboard``. Browsers cannot send custom headers on EventSource — in
production-Bearer mode the connection 401s and the polling fallback covers
liveness. In dev/cookie-auth deploys (or LAN-only same-origin), the SSE
stream works directly.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

router = APIRouter()


@router.get("/qa/runs/{run_id}/events")
async def stream_run_events(run_id: str, request: Request) -> EventSourceResponse:
    """Server-Sent Events stream of QA state changes for a single run.

    Closes when the run finishes (final ``done`` event) or the client
    disconnects. Events are JSON-encoded; clients parse ``event.data``.

    Returns 503 if the QA event bus is not initialised — happens in tests
    that build a FastAPI app without entering the lifespan, and in any
    misconfigured deploy that skipped ``app.state.qa_event_bus = ...``.
    """
    bus = getattr(request.app.state, "qa_event_bus", None)
    if bus is None:
        raise HTTPException(status_code=503, detail="qa event bus not initialised")

    async def event_stream() -> AsyncIterator[dict[str, Any]]:
        # ``bus.subscribe`` returns an async generator that registers a queue
        # on entry and prunes it in a finally-block on exit. Iterating with
        # ``async for`` ensures the cleanup runs even when the client
        # disconnects (sse-starlette cancels the task, which raises
        # CancelledError into the await — propagated through the generator
        # finally so the channel doesn't leak).
        async for event in bus.subscribe(run_id):
            # Belt-and-suspenders disconnect check; sse-starlette also
            # cancels the task on disconnect, but checking explicitly lets
            # us close the iterator cleanly between events.
            if await request.is_disconnected():
                return
            yield {"data": json.dumps(event)}
            if event.get("type") == "done":
                return

    return EventSourceResponse(event_stream())
