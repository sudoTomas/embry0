"""Route tests for the SSE liveness endpoint.

Endpoint: GET /api/v1/qa/runs/{run_id}/events

Coverage:
  1. Returns 503 when app.state.qa_event_bus is missing.
  2. Streams JSON-encoded events in publish order, then closes after a
     'done' event.
  3. Requires Bearer auth (401 without headers).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from athanor.qa.event_bus import QAEventBus

_KEY = "test-api-key-32-characters-minimum-x"
_AUTH = {"Authorization": f"Bearer {_KEY}"}


def _make_app(*, with_bus: bool = True):
    """Build a bare FastAPI app without entering the lifespan, mirroring
    the pattern from ``test_qa_dashboard_routes.py``. ``app.state`` is
    populated manually so each test can control the bus presence."""
    from athanor.api.app import create_app

    app = create_app()

    class _Cfg:
        pass

    cfg = _Cfg()
    cfg.api_key = _KEY
    cfg.auth_dev_mode = False
    cfg.webhook_dev_mode = False
    app.state.config = cfg

    if with_bus:
        app.state.qa_event_bus = QAEventBus()
    elif hasattr(app.state, "qa_event_bus"):
        # The lifespan never ran in this test path, but defensively clear
        # any attribute that might have been populated by create_app().
        delattr(app.state, "qa_event_bus")

    return app, TestClient(app)


# ---------------------------------------------------------------------------
# Test 1: 503 when bus is missing
# ---------------------------------------------------------------------------


def test_sse_route_returns_503_when_bus_missing() -> None:
    _, client = _make_app(with_bus=False)
    resp = client.get("/api/v1/qa/runs/run-1/events", headers=_AUTH)
    assert resp.status_code == 503
    assert "qa event bus" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Test 2: streams events until 'done', then closes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_route_streams_events_until_done() -> None:
    """End-to-end: publish two subtask_status + a done; the route yields
    them in order and the response body terminates after 'done'."""
    app, _ = _make_app(with_bus=True)
    bus: QAEventBus = app.state.qa_event_bus

    # We invoke the route handler directly with a stub Request rather than
    # spinning up TestClient — sync TestClient blocks the event loop while
    # consuming the streamed body, which would prevent the publishing
    # coroutine below from running. Direct invocation lets us schedule
    # publishes between iterator advances on the same loop.
    from starlette.requests import Request

    from athanor.api.v1.qa_events import stream_run_events

    # Minimal ASGI scope; ``is_disconnected()`` reads from ``receive``.
    async def fake_receive():
        # Block forever — the route uses ``request.is_disconnected()`` which
        # checks the receive channel. Returning a "lifespan" type would
        # confuse it; the simplest correct stub is to never resolve.
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}  # pragma: no cover

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/qa/runs/run-events-1/events",
        "headers": [],
        "app": app,
    }
    request = Request(scope, fake_receive)
    response = await stream_run_events(run_id="run-events-1", request=request)

    body_iter = response.body_iterator

    async def publish_after_subscribe() -> None:
        # Yield once so the route's async-for has registered its queue
        # before we publish — otherwise the publish would land before the
        # subscriber and be dropped (no replay buffer).
        await asyncio.sleep(0.05)
        await bus.publish(
            "run-events-1",
            {"type": "subtask_status", "app": "hub", "status": "passed"},
        )
        await bus.publish(
            "run-events-1",
            {"type": "subtask_status", "app": "companion", "status": "failed"},
        )
        await bus.publish(
            "run-events-1",
            {"type": "done", "run_id": "run-events-1", "overall_status": "failed"},
        )

    pub_task = asyncio.create_task(publish_after_subscribe())

    # sse-starlette's EventSourceResponse.body_iterator yields the dicts the
    # route's async-generator yields (each ``{"data": "..."}``) — not the
    # ASGI bytes those eventually serialise to. The route appends a single
    # ``data`` key per event, so we just JSON-decode each one.
    chunks: list[dict] = []
    try:
        async for chunk in body_iter:
            chunks.append(chunk)
    finally:
        await pub_task

    events = [json.loads(c["data"]) for c in chunks if "data" in c]
    types = [e.get("type") for e in events]
    assert types == ["subtask_status", "subtask_status", "done"]
    assert events[0]["app"] == "hub"
    assert events[1]["app"] == "companion"
    assert events[2]["overall_status"] == "failed"


# ---------------------------------------------------------------------------
# Test 3: auth gate
# ---------------------------------------------------------------------------


def test_sse_route_requires_auth() -> None:
    _, client = _make_app(with_bus=True)
    # No Authorization header — the qa_events router shares ``require_auth``
    # with qa_dashboard, so the response is 401 (not 503), proving the auth
    # dependency runs before the bus check.
    resp = client.get("/api/v1/qa/runs/run-1/events")
    assert resp.status_code == 401
