import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from athanor.api.app import create_app
from athanor.config import AthanorConfig


@pytest.fixture
def app():
    config = AthanorConfig(_env_file=None, dev_mode=True, github_webhook_secret="test-secret")
    app = create_app(config)
    # Mock required state
    mock_issues_repo = MagicMock()
    mock_github_sync = MagicMock()
    mock_github_sync.handle_webhook_event = AsyncMock(return_value={"status": "ignored"})
    app.state.issues_repo = mock_issues_repo
    app.state.inputs_repo = MagicMock()
    app.state.github_sync = mock_github_sync
    app.state.issue_executor = MagicMock()
    return app


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_webhook_valid_signature(app):
    payload = json.dumps({"action": "labeled", "issue": {"number": 1}}).encode()
    sig = _sign(payload, "test-secret")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhook",
            content=payload,
            headers={
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "issues",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_webhook_invalid_signature(app):
    payload = b'{"action": "labeled"}'
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhook",
            content=payload,
            headers={
                "X-Hub-Signature-256": "sha256=invalid",
                "X-GitHub-Event": "issues",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_no_secret_dev_mode_accepts(app):
    """DEV_MODE=true and no secret configured: accept (smee.io local dev flow)."""
    app.state.config = AthanorConfig(_env_file=None, dev_mode=True, github_webhook_secret="")
    payload = json.dumps({"action": "opened", "issue": {"number": 1}, "repository": {"full_name": "o/r"}}).encode()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhook",
            content=payload,
            headers={
                "X-GitHub-Event": "issues",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_webhook_smee_envelope_unwrapped(app):
    """Smee.io wraps the real payload in {'payload': '<json-string>'}. Handler must unwrap."""
    # Dev-mode app with NO secret, simulating local smee flow
    app.state.config = AthanorConfig(_env_file=None, dev_mode=True, github_webhook_secret="")

    real_payload = {"action": "labeled", "issue": {"number": 42}, "repository": {"full_name": "o/r"}}
    smee_envelope = {"payload": json.dumps(real_payload)}
    body = json.dumps(smee_envelope).encode()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "issues",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 200
    # github_sync.handle_webhook_event should have been called with the UNWRAPPED payload
    app.state.github_sync.handle_webhook_event.assert_awaited_once()
    kwargs = app.state.github_sync.handle_webhook_event.await_args.kwargs
    assert kwargs["action"] == "labeled"
    assert kwargs["payload"]["issue"]["number"] == 42


@pytest.mark.asyncio
async def test_webhook_dev_mode_no_secret_accepts_unsigned(app):
    """DEV_MODE=true with no secret accepts unsigned webhooks (smee flow)."""
    app.state.config = AthanorConfig(_env_file=None, dev_mode=True, github_webhook_secret="")
    payload = json.dumps({"action": "opened", "issue": {"number": 1}, "repository": {"full_name": "o/r"}}).encode()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhook",
            content=payload,
            headers={
                "X-GitHub-Event": "issues",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_webhook_prod_mode_no_secret_rejects_with_503(app):
    """Without dev_mode and no secret, webhook is rejected with 503."""
    app.state.config = AthanorConfig(_env_file=None, dev_mode=False, github_webhook_secret="")
    payload = b'{"action": "opened"}'
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhook",
            content=payload,
            headers={
                "X-GitHub-Event": "issues",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_webhook_non_envelope_payload_field_preserved(app):
    """A real GitHub payload whose top-level 'payload' is not JSON must pass through unchanged.

    Regression guard: the smee envelope unwrap silently swallows JSONDecodeError, which is
    the intended fallback behavior. Without this test a refactor that changes the fallback to
    raise 400 would go unnoticed.
    """
    app.state.config = AthanorConfig(_env_file=None, dev_mode=True, github_webhook_secret="")
    real = {
        "action": "labeled",
        "payload": "not-json-at-all",  # looks like a smee envelope field but isn't
        "issue": {"number": 99},
        "repository": {"full_name": "o/r"},
    }
    body = json.dumps(real).encode()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "issues",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 200
    # Handler received the ORIGINAL payload (not an exception, not a rewrite)
    app.state.github_sync.handle_webhook_event.assert_awaited_once()
    kwargs = app.state.github_sync.handle_webhook_event.await_args.kwargs
    assert kwargs["action"] == "labeled"
    assert kwargs["payload"]["issue"]["number"] == 99
    assert kwargs["payload"]["payload"] == "not-json-at-all"  # original field survived


@pytest.mark.asyncio
async def test_webhook_does_not_recursively_unwrap_nested_envelopes(app):
    """Handler unwraps smee envelope exactly once.

    If a payload contains a literal nested envelope (rare; not produced by smee),
    the inner envelope must be passed through to downstream handlers rather than
    silently recursively unwrapped — preventing unexpected attack surface if a
    crafted payload is ever delivered via an unsigned route.
    """
    app.state.config = AthanorConfig(_env_file=None, dev_mode=True, github_webhook_secret="")

    inner_payload = {"action": "opened", "issue": {"number": 1}, "repository": {"full_name": "o/r"}}
    # A single envelope whose inner "payload" string is itself a valid envelope
    nested_inner = {"payload": json.dumps(inner_payload)}
    outer_envelope = {"payload": json.dumps(nested_inner)}
    body = json.dumps(outer_envelope).encode()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "issues",
                "Content-Type": "application/json",
            },
        )
    # The handler unwrapped ONCE — downstream sees the intermediate envelope,
    # which has no "action" key, so the handler returns "ignored" (not 200 success
    # via the real payload).
    assert resp.status_code == 200
    body_json = resp.json()
    assert body_json.get("status") == "ignored", (
        f"Double-unwrap would have delivered the real payload. Got: {body_json}"
    )
