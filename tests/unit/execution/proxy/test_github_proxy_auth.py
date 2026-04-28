"""Tests for github_proxy bearer auth + admin enrollment."""

from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from athanor.execution.proxy.github_proxy import create_github_proxy_app

ADMIN = "test-admin-secret-not-real"
PAT = "ghp_PAT_TESTING_ONLY"

# Tokens used in tests must be >= 32 chars per enrollment validation
TOK_S1 = "tok-s1-" + "a" * 30  # 37 chars
TOK_S2 = "tok-s2-" + "b" * 30  # 37 chars
TOK_A = "token-A-" + "a" * 30  # 38 chars
TOK_B = "token-B-" + "b" * 30  # 38 chars


@pytest.fixture
async def client():
    app = create_github_proxy_app(github_token=PAT, admin_token=ADMIN)
    server = TestServer(app)
    async with TestClient(server) as c:
        yield c


async def test_health_open(client):
    resp = await client.get("/health")
    assert resp.status == 200


async def test_proxy_without_bearer_rejected(client):
    resp = await client.get("/repos/owner/repo/issues/1")
    assert resp.status == 401


async def test_proxy_with_unenrolled_bearer_rejected(client):
    resp = await client.get(
        "/repos/owner/repo/issues/1",
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
    """Enrolled bearer is forwarded; sandbox Authorization header is stripped."""
    enroll = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK_S1},
        headers={"X-Admin-Token": ADMIN},
    )
    assert enroll.status == 200

    # Build a mock httpx response
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"number": 1}'
    mock_resp.headers = {"content-type": "application/json"}

    captured: list[dict] = []

    async def fake_request(method, url, headers, content):
        captured.append({"method": method, "url": url, "headers": dict(headers)})
        return mock_resp

    with patch.object(
        client.app["http_client"], "request", side_effect=fake_request
    ):
        resp = await client.get(
            "/repos/owner/repo/issues/1",
            headers={"Authorization": f"Bearer {TOK_S1}"},
        )

    assert resp.status == 200

    assert len(captured) == 1
    fwd_headers = captured[0]["headers"]
    # PAT must be injected
    assert fwd_headers.get("Authorization") == f"Bearer {PAT}"
    # Sandbox bearer must NOT appear in forwarded headers
    assert TOK_S1 not in str(fwd_headers)


async def test_multi_tenant_both_work(client):
    for sid, tok in [("s1", TOK_S1), ("s2", TOK_S2)]:
        enroll = await client.post(
            "/admin/enroll",
            json={"sandbox_id": sid, "sandbox_token": tok},
            headers={"X-Admin-Token": ADMIN},
        )
        assert enroll.status == 200

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"{}"
    mock_resp.headers = {"content-type": "application/json"}

    with patch.object(
        client.app["http_client"], "request", return_value=mock_resp
    ):
        for tok in (TOK_S1, TOK_S2):
            resp = await client.get(
                "/repos/owner/repo",
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

    resp = await client.get(
        "/repos/owner/repo",
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

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"{}"
    mock_resp.headers = {"content-type": "application/json"}

    with patch.object(
        client.app["http_client"], "request", return_value=mock_resp
    ):
        # Token A should work
        resp = await client.get(
            "/repos/owner/repo", headers={"Authorization": f"Bearer {TOK_A}"}
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
    resp = await client.get(
        "/repos/owner/repo", headers={"Authorization": f"Bearer {TOK_A}"}
    )
    assert resp.status == 401

    with patch.object(
        client.app["http_client"], "request", return_value=mock_resp
    ):
        # Token B must work
        resp = await client.get(
            "/repos/owner/repo", headers={"Authorization": f"Bearer {TOK_B}"}
        )
        assert resp.status == 200

    # Unenroll s1 — both dicts should be clean
    resp = await client.delete("/admin/enroll/s1", headers={"X-Admin-Token": ADMIN})
    assert resp.status == 200

    resp = await client.get(
        "/repos/owner/repo", headers={"Authorization": f"Bearer {TOK_B}"}
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
        create_github_proxy_app(github_token="x", admin_token="")
