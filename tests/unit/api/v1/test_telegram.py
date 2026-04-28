"""Tests for the Telegram callback handler's auth posture."""

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from athanor.api.v1.telegram import router


def _make_app(secret: str, inputs_repo=None, issues_repo=None, executor=None, config=None):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.telegram_webhook_secret = secret
    app.state.inputs_repo = inputs_repo or MagicMock()
    app.state.issues_repo = issues_repo or MagicMock()
    app.state.issue_executor = executor or MagicMock()
    app.state.config = config or MagicMock(telegram_bot_token="", telegram_chat_id="")
    return app


def test_callback_returns_503_when_secret_empty():
    """Empty secret → 503, never accept."""
    app = _make_app(secret="")
    client = TestClient(app)
    resp = client.post("/api/v1/telegram/callback", json={"update_id": 1})
    assert resp.status_code == 503
    assert resp.json()["error"] == "telegram_callback_unconfigured"


def test_callback_returns_401_when_secret_mismatched():
    app = _make_app(secret="real-secret")
    client = TestClient(app)
    resp = client.post(
        "/api/v1/telegram/callback",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert resp.status_code == 401


def test_callback_passes_when_secret_matches():
    """Correct secret → 200; downstream logic returns 'ignored' for empty payload."""
    app = _make_app(secret="real-secret")
    client = TestClient(app)
    resp = client.post(
        "/api/v1/telegram/callback",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "real-secret"},
    )
    assert resp.status_code == 200
    # The handler returns "ignored" because there's no reply_to_message
    assert resp.json()["status"] == "ignored"
