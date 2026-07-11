"""Tests for auth_proxy bearer auth + admin enrollment (streaming)."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from aiohttp.test_utils import TestClient, TestServer

from embry0.execution.proxy.auth_proxy import create_auth_proxy_app

ADMIN = "test-admin-secret-not-real"
API_KEY = "sk-ant-test-key-XXXXXXXXXXXXXXXXXXXXXXXX"

# Tokens used in tests must be >= 32 chars per enrollment validation
TOK_S1 = "tok-s1-" + "a" * 30  # 37 chars
TOK_S2 = "tok-s2-" + "b" * 30  # 37 chars
TOK_A = "token-A-" + "a" * 30  # 38 chars
TOK_B = "token-B-" + "b" * 30  # 38 chars


# ---------------------------------------------------------------------------
# Streaming mock helpers
# ---------------------------------------------------------------------------


class FakeStreamResp:
    def __init__(self, status_code=200, body=b'{"ok":true}', headers=None):
        self.status_code = status_code
        self.headers = headers or {"content-type": "application/json"}
        self._body = body

    async def aiter_bytes(self):
        yield self._body


class FakeStreamCtx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *a):
        return None


def make_fake_stream(status_code=200, body=b'{"ok":true}', headers=None):
    """Return a mock client whose .stream() method yields a FakeStreamResp."""
    resp = FakeStreamResp(status_code=status_code, body=body, headers=headers)
    fake_client = MagicMock()
    fake_client.stream = MagicMock(return_value=FakeStreamCtx(resp))
    return fake_client, resp


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def client():
    app = create_auth_proxy_app(api_key=API_KEY, admin_token=ADMIN)
    server = TestServer(app)
    async with TestClient(server) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_health_open(client):
    resp = await client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["proxy"] == "auth"


async def test_proxy_without_bearer_rejected(client):
    resp = await client.post("/v1/messages", json={"model": "claude-3-5-haiku-20241022"})
    assert resp.status == 401


async def test_proxy_with_unenrolled_bearer_rejected(client):
    resp = await client.post(
        "/v1/messages",
        json={"model": "claude-3-5-haiku-20241022"},
        headers={"Authorization": "Bearer bogus"},
    )
    assert resp.status == 401


async def test_enroll_requires_admin_header(client):
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK_S1},
    )
    assert resp.status == 401


async def test_enroll_with_wrong_admin_rejected(client):
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK_S1},
        headers={"X-Admin-Token": "wrong"},
    )
    assert resp.status == 401


async def test_enrolled_bearer_succeeds(client):
    """Enrolled bearer is forwarded; sandbox Authorization header is stripped; x-api-key injected."""
    enroll = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK_S1},
        headers={"X-Admin-Token": ADMIN},
    )
    assert enroll.status == 200

    captured: list[dict] = []

    class CapturingStreamCtx:
        async def __aenter__(self_inner):
            return FakeStreamResp(status_code=200, body=b'{"ok":true}')

        async def __aexit__(self_inner, *a):
            return None

    def fake_stream(method, url, headers, content):
        captured.append({"method": method, "url": url, "headers": dict(headers)})
        return CapturingStreamCtx()

    with patch.object(client.app["http_client"], "stream", side_effect=fake_stream):
        resp = await client.post(
            "/v1/messages",
            json={"model": "claude-3-5-haiku-20241022"},
            headers={"Authorization": f"Bearer {TOK_S1}"},
        )

    assert resp.status == 200

    assert len(captured) == 1
    fwd_headers = captured[0]["headers"]

    # x-api-key must be injected
    assert fwd_headers.get("x-api-key") == API_KEY
    # Sandbox bearer must NOT appear in forwarded headers
    assert TOK_S1 not in str(fwd_headers)
    # Authorization header must be stripped entirely
    assert "Authorization" not in fwd_headers
    assert "authorization" not in fwd_headers

    # Verify streamed body is returned to caller
    body = await resp.text()
    assert "ok" in body


async def test_multi_tenant_both_work(client):
    for sid, tok in [("s1", TOK_S1), ("s2", TOK_S2)]:
        enroll = await client.post(
            "/admin/enroll",
            json={"sandbox_id": sid, "sandbox_token": tok},
            headers={"X-Admin-Token": ADMIN},
        )
        assert enroll.status == 200

    fake_client, _ = make_fake_stream()

    with patch.object(client.app["http_client"], "stream", fake_client.stream):
        for tok in (TOK_S1, TOK_S2):
            resp = await client.post(
                "/v1/messages",
                json={"model": "claude-3-5-haiku-20241022"},
                headers={"Authorization": f"Bearer {tok}"},
            )
            assert resp.status == 200


async def test_unenroll_revokes(client):
    await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK_S1},
        headers={"X-Admin-Token": ADMIN},
    )
    resp = await client.delete("/admin/enroll/s1", headers={"X-Admin-Token": ADMIN})
    assert resp.status == 200

    resp = await client.post(
        "/v1/messages",
        json={"model": "claude-3-5-haiku-20241022"},
        headers={"Authorization": f"Bearer {TOK_S1}"},
    )
    assert resp.status == 401


async def test_re_enroll_replaces_old_token(client):
    """Re-enrolling sandbox_id with a new token invalidates the old one."""
    # Enroll with token A
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK_A},
        headers={"X-Admin-Token": ADMIN},
    )
    assert resp.status == 200

    fake_client, _ = make_fake_stream()

    with patch.object(client.app["http_client"], "stream", fake_client.stream):
        # Token A should work
        resp = await client.post(
            "/v1/messages",
            json={"model": "claude-3-5-haiku-20241022"},
            headers={"Authorization": f"Bearer {TOK_A}"},
        )
        assert resp.status == 200

    # Re-enroll s1 with token B
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK_B},
        headers={"X-Admin-Token": ADMIN},
    )
    assert resp.status == 200

    # Token A must now be rejected
    resp = await client.post(
        "/v1/messages",
        json={"model": "claude-3-5-haiku-20241022"},
        headers={"Authorization": f"Bearer {TOK_A}"},
    )
    assert resp.status == 401

    fake_client2, _ = make_fake_stream()

    with patch.object(client.app["http_client"], "stream", fake_client2.stream):
        # Token B must work
        resp = await client.post(
            "/v1/messages",
            json={"model": "claude-3-5-haiku-20241022"},
            headers={"Authorization": f"Bearer {TOK_B}"},
        )
        assert resp.status == 200

    # Unenroll s1 — both dicts should be clean
    resp = await client.delete("/admin/enroll/s1", headers={"X-Admin-Token": ADMIN})
    assert resp.status == 200

    resp = await client.post(
        "/v1/messages",
        json={"model": "claude-3-5-haiku-20241022"},
        headers={"Authorization": f"Bearer {TOK_B}"},
    )
    assert resp.status == 401


async def test_enroll_rejects_short_sandbox_token(client):
    """sandbox_token shorter than 32 chars must be rejected with 400."""
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": "x"},
        headers={"X-Admin-Token": ADMIN},
    )
    assert resp.status == 400


async def test_enroll_rejects_malformed_sandbox_id(client):
    """sandbox_id with path-traversal characters must be rejected with 400."""
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "../../../etc/passwd", "sandbox_token": TOK_S1},
        headers={"X-Admin-Token": ADMIN},
    )
    assert resp.status == 400

    # Also reject a sandbox_id longer than 64 characters
    long_id = "a" * 65
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": long_id, "sandbox_token": TOK_S1},
        headers={"X-Admin-Token": ADMIN},
    )
    assert resp.status == 400


async def test_create_app_rejects_empty_admin():
    with pytest.raises(ValueError, match="admin_token is required"):
        create_auth_proxy_app(api_key="sk-ant-x", admin_token="")


async def test_proxy_connect_error_returns_502(client):
    """Upstream connect error to api.anthropic.com surfaces as 502."""
    enroll = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK_S1},
        headers={"X-Admin-Token": ADMIN},
    )
    assert enroll.status == 200

    with patch.object(
        client.app["http_client"],
        "stream",
        side_effect=httpx.ConnectError("refused"),
    ):
        resp = await client.post(
            "/v1/messages",
            json={"model": "claude-3-5-haiku-20241022"},
            headers={"Authorization": f"Bearer {TOK_S1}"},
        )
    assert resp.status == 502


async def test_proxy_timeout_returns_504(client):
    """Upstream timeout to api.anthropic.com surfaces as 504."""
    enroll = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK_S1},
        headers={"X-Admin-Token": ADMIN},
    )
    assert enroll.status == 200

    with patch.object(
        client.app["http_client"],
        "stream",
        side_effect=httpx.TimeoutException("timeout"),
    ):
        resp = await client.post(
            "/v1/messages",
            json={"model": "claude-3-5-haiku-20241022"},
            headers={"Authorization": f"Bearer {TOK_S1}"},
        )
    assert resp.status == 504


async def test_proxy_strips_incoming_x_api_key(client):
    """Sandbox-supplied X-Api-Key header must be stripped before the real key is injected."""
    enroll = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK_S1},
        headers={"X-Admin-Token": ADMIN},
    )
    assert enroll.status == 200

    captured: list[dict] = []

    class CapturingStreamCtx:
        async def __aenter__(self_inner):
            return FakeStreamResp(status_code=200, body=b'{"ok":true}')

        async def __aexit__(self_inner, *a):
            return None

    def fake_stream(method, url, headers, content):
        captured.append({"headers": dict(headers)})
        return CapturingStreamCtx()

    with patch.object(client.app["http_client"], "stream", side_effect=fake_stream):
        resp = await client.post(
            "/v1/messages",
            json={"model": "claude-3-5-haiku-20241022"},
            headers={
                "Authorization": f"Bearer {TOK_S1}",
                "X-Api-Key": "bogus-key-from-sandbox",
            },
        )

    assert resp.status == 200
    assert len(captured) == 1
    fwd_headers = captured[0]["headers"]

    # Exactly one x-api-key entry, and it must be the real key (not the bogus one)
    api_key_values = [v for k, v in fwd_headers.items() if k.lower() == "x-api-key"]
    assert api_key_values == [API_KEY], f"Expected exactly one x-api-key={API_KEY!r}, got {api_key_values!r}"
    assert "bogus-key-from-sandbox" not in str(fwd_headers)


async def test_proxy_handles_mid_stream_read_error(client, capsys):
    """A TCP drop mid-stream logs a warning and truncates gracefully (status already committed)."""
    enroll = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK_S1},
        headers={"X-Admin-Token": ADMIN},
    )
    assert enroll.status == 200

    PARTIAL_CHUNK = b'{"partial":'

    class ErrorAfterOneChunkResp:
        status_code = 200
        headers = {"content-type": "application/json"}

        async def aiter_bytes(self):
            yield PARTIAL_CHUNK
            raise httpx.ReadError("connection dropped")

    class ErrorStreamCtx:
        async def __aenter__(self):
            return ErrorAfterOneChunkResp()

        async def __aexit__(self, *a):
            return None

    with patch.object(
        client.app["http_client"],
        "stream",
        return_value=ErrorStreamCtx(),
    ):
        resp = await client.post(
            "/v1/messages",
            json={"model": "claude-3-5-haiku-20241022"},
            headers={"Authorization": f"Bearer {TOK_S1}"},
        )

    # Response was already prepared with 200 before the error
    assert resp.status == 200

    # Partial body was flushed before the error
    body = await resp.read()
    assert PARTIAL_CHUNK in body

    # structlog writes to stdout by default; verify the warning was emitted
    captured = capsys.readouterr()
    assert "auth_proxy_stream_truncated" in captured.out, (
        f"Expected auth_proxy_stream_truncated in stdout, got: {captured.out!r}"
    )


async def test_proxy_forwards_upstream_404_streaming(client):
    """A 404 from api.anthropic.com is forwarded as-is via the streaming path."""
    enroll = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK_S1},
        headers={"X-Admin-Token": ADMIN},
    )
    assert enroll.status == 200

    fake_client, _ = make_fake_stream(
        status_code=404,
        body=b'{"error":{"type":"not_found_error","message":"Not found"}}',
        headers={"content-type": "application/json"},
    )

    with patch.object(client.app["http_client"], "stream", fake_client.stream):
        resp = await client.post(
            "/v1/messages",
            json={"model": "claude-3-5-haiku-20241022"},
            headers={"Authorization": f"Bearer {TOK_S1}"},
        )

    assert resp.status == 404
    body = await resp.json()
    assert body["error"]["type"] == "not_found_error"
