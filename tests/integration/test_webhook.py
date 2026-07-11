"""Integration tests for webhook processing."""

import hashlib
import hmac
import json

import pytest
from httpx import AsyncClient


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _headers(payload: bytes, secret: str, event: str = "issues") -> dict:
    return {
        "X-Hub-Signature-256": _sign(payload, secret),
        "X-GitHub-Event": event,
        "Content-Type": "application/json",
    }


@pytest.mark.asyncio
async def test_webhook_labeled_issue_triggers(app: AsyncClient):
    """A labeled event on a tracked issue returns accepted."""
    # First open the issue so it is tracked.
    open_payload = json.dumps(
        {
            "action": "opened",
            "repository": {"full_name": "owner/repo"},
            "issue": {"number": 42, "title": "Fix auth", "body": "", "labels": [], "html_url": ""},
        }
    ).encode()
    resp = await app.post(
        "/api/v1/webhook",
        content=open_payload,
        headers=_headers(open_payload, "test-secret"),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"

    # Now label it with the trigger label.
    label_payload = json.dumps(
        {
            "action": "labeled",
            "repository": {"full_name": "owner/repo"},
            "issue": {"number": 42, "title": "Fix auth", "labels": [{"name": "embry0"}]},
        }
    ).encode()
    resp = await app.post(
        "/api/v1/webhook",
        content=label_payload,
        headers=_headers(label_payload, "test-secret"),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


@pytest.mark.asyncio
async def test_webhook_non_matching_label_ignored(app: AsyncClient):
    """An untracked issue labeled with a non-trigger label is ignored."""
    payload = json.dumps(
        {
            "action": "labeled",
            "repository": {"full_name": "owner/repo"},
            "issue": {"number": 99, "labels": [{"name": "bug"}]},
        }
    ).encode()
    resp = await app.post(
        "/api/v1/webhook",
        content=payload,
        headers=_headers(payload, "test-secret"),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_webhook_invalid_signature_rejected(app: AsyncClient):
    resp = await app.post(
        "/api/v1/webhook",
        content=b'{"action": "labeled"}',
        headers={
            "X-Hub-Signature-256": "sha256=invalid",
            "X-GitHub-Event": "issues",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401
