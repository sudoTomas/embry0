"""Tests for the /queue endpoint with real counts."""

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from athanor.api.v1.queue import router


def _app(summary: dict[str, int]) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    jobs_repo = MagicMock()
    jobs_repo.queue_summary = AsyncMock(return_value=summary)
    app.state.jobs_repo = jobs_repo
    return app


def test_queue_returns_per_status_counts():
    app = _app({"pending": 3, "running": 1, "awaiting_input": 2, "paused": 0})
    client = TestClient(app)
    resp = client.get("/api/v1/queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "depth": 3,
        "pending": 3,
        "running": 1,
        "awaiting_input": 2,
        "paused": 0,
    }


def test_queue_returns_zeros_when_empty():
    app = _app({"pending": 0, "running": 0, "awaiting_input": 0, "paused": 0})
    client = TestClient(app)
    resp = client.get("/api/v1/queue")
    body = resp.json()
    assert body["depth"] == 0
    assert body["pending"] == 0
    assert body["running"] == 0
    assert body["awaiting_input"] == 0
    assert body["paused"] == 0


def test_queue_depth_is_alias_for_pending():
    """depth must always equal pending (back-compat alias)."""
    app = _app({"pending": 5, "running": 2, "awaiting_input": 1, "paused": 3})
    client = TestClient(app)
    body = client.get("/api/v1/queue").json()
    assert body["depth"] == body["pending"]


def test_queue_paused_is_int_not_bool():
    """paused is now a count (int), not a boolean."""
    app = _app({"pending": 0, "running": 0, "awaiting_input": 0, "paused": 4})
    client = TestClient(app)
    body = client.get("/api/v1/queue").json()
    assert body["paused"] == 4
    assert isinstance(body["paused"], int)
