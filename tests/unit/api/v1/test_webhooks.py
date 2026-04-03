import hashlib
import hmac
import json

import pytest
from httpx import ASGITransport, AsyncClient

from legion.api.app import create_app
from legion.config import LegionConfig


@pytest.fixture
def app():
    config = LegionConfig(_env_file=None, dev_mode=True, github_webhook_secret="test-secret")
    return create_app(config)


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_webhook_valid_signature(app):
    payload = json.dumps({"action": "labeled", "issue": {"number": 1}}).encode()
    sig = _sign(payload, "test-secret")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook", content=payload, headers={
            "X-Hub-Signature-256": sig, "X-GitHub-Event": "issues", "Content-Type": "application/json",
        })
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_webhook_invalid_signature(app):
    payload = b'{"action": "labeled"}'
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook", content=payload, headers={
            "X-Hub-Signature-256": "sha256=invalid", "X-GitHub-Event": "issues", "Content-Type": "application/json",
        })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_no_secret_configured(app):
    app.state.config = LegionConfig(_env_file=None, dev_mode=True, github_webhook_secret="")
    payload = b'{"action": "labeled"}'
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook", content=payload, headers={
            "X-GitHub-Event": "issues", "Content-Type": "application/json",
        })
    assert resp.status_code == 200
