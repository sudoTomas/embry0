"""Integration tests for webhook processing."""

import hashlib
import hmac
import json

import pytest
from httpx import AsyncClient


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_webhook_labeled_issue_triggers(app: AsyncClient):
    payload = json.dumps(
        {
            "action": "labeled",
            "issue": {"number": 42, "title": "Fix auth", "labels": [{"name": "Athanor"}]},
        }
    ).encode()
    sig = _sign(payload, "test-secret")

    resp = await app.post(
        "/webhook",
        content=payload,
        headers={
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "issues",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


@pytest.mark.asyncio
async def test_webhook_non_matching_label_ignored(app: AsyncClient):
    payload = json.dumps(
        {
            "action": "labeled",
            "issue": {"number": 43, "labels": [{"name": "bug"}]},
        }
    ).encode()
    sig = _sign(payload, "test-secret")

    resp = await app.post(
        "/webhook",
        content=payload,
        headers={
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "issues",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_webhook_invalid_signature_rejected(app: AsyncClient):
    resp = await app.post(
        "/webhook",
        content=b'{"action": "labeled"}',
        headers={
            "X-Hub-Signature-256": "sha256=invalid",
            "X-GitHub-Event": "issues",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401
