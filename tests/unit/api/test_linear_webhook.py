"""POST /api/v1/webhook/linear — signature rules + dispatch wiring (EMB-47)."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config

_PAYLOAD = {"type": "Issue", "action": "create", "data": {"id": "uuid-1", "identifier": "EMB-48"}}


class _FakeLinearSync:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def handle_webhook_event(self, payload, *, issues_repo, issue_executor, dashboard_base_url=""):
        self.calls.append(payload)
        return {"status": "accepted", "action": "dispatched"}


def _app(*, webhook_dev_mode: bool, secret: str = "", wire_sync: bool = True):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _noop_lifespan(app):
        yield

    config = Embry0Config(
        database_url="postgresql://unused",
        auth_dev_mode=True,
        webhook_dev_mode=webhook_dev_mode,
        linear_webhook_secret=secret,
    )
    app = create_app(config, lifespan_override=_noop_lifespan)
    app.state.config = config
    app.state.issues_repo = object()
    app.state.issue_executor = object()
    sync = _FakeLinearSync() if wire_sync else None
    app.state.linear_sync = sync
    return app, sync


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_dev_mode_bypasses_signature() -> None:
    app, sync = _app(webhook_dev_mode=True)
    async with _client(app) as client:
        r = await client.post("/api/v1/webhook/linear", json=_PAYLOAD)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"
    assert sync.calls and sync.calls[0]["type"] == "Issue"


async def test_valid_signature_accepted() -> None:
    secret = "shh-linear"
    app, sync = _app(webhook_dev_mode=False, secret=secret)
    body = json.dumps(_PAYLOAD).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    async with _client(app) as client:
        r = await client.post(
            "/api/v1/webhook/linear",
            content=body,
            headers={"Linear-Signature": sig, "Content-Type": "application/json"},
        )
    assert r.status_code == 200, r.text
    assert sync.calls


@pytest.mark.parametrize("sig", ["", "deadbeef"])
async def test_bad_signature_rejected(sig: str) -> None:
    app, _ = _app(webhook_dev_mode=False, secret="shh-linear")
    async with _client(app) as client:
        r = await client.post(
            "/api/v1/webhook/linear",
            content=json.dumps(_PAYLOAD).encode(),
            headers={"Linear-Signature": sig, "Content-Type": "application/json"},
        )
    assert r.status_code == 401


async def test_missing_secret_is_503() -> None:
    app, _ = _app(webhook_dev_mode=False, secret="")
    async with _client(app) as client:
        r = await client.post("/api/v1/webhook/linear", json=_PAYLOAD)
    assert r.status_code == 503


async def test_unconfigured_integration_ignores() -> None:
    app, _ = _app(webhook_dev_mode=True, wire_sync=False)
    async with _client(app) as client:
        r = await client.post("/api/v1/webhook/linear", json=_PAYLOAD)
    assert r.status_code == 200
    assert r.json()["reason"] == "linear integration not configured"


async def test_smee_envelope_unwrapped() -> None:
    app, sync = _app(webhook_dev_mode=True)
    envelope = {"payload": json.dumps(_PAYLOAD)}
    async with _client(app) as client:
        r = await client.post("/api/v1/webhook/linear", json=envelope)
    assert r.status_code == 200
    assert sync.calls and sync.calls[0]["type"] == "Issue"
