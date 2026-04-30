"""Tests for git_proxy bearer auth + admin enrollment."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from athanor.execution.proxy.git_proxy import create_git_proxy_app

ADMIN = "test-admin-secret-not-real"
PAT = "ghp_PAT_TESTING_ONLY"

# Tokens used in tests must be >= 32 chars per enrollment validation
TOK_S1 = "tok-s1-" + "a" * 30  # 37 chars
TOK_S2 = "tok-s2-" + "b" * 30  # 37 chars
TOK_A = "token-A-" + "a" * 30  # 38 chars
TOK_B = "token-B-" + "b" * 30  # 38 chars


@pytest.fixture
async def client():
    app = create_git_proxy_app(github_token=PAT, admin_token=ADMIN)
    server = TestServer(app)
    async with TestClient(server) as c:
        yield c


async def test_health_open(client):
    resp = await client.get("/health")
    assert resp.status == 200


async def test_credentials_without_bearer_rejected(client):
    resp = await client.get("/git-credentials")
    assert resp.status == 401


async def test_credentials_with_unenrolled_bearer_rejected(client):
    resp = await client.get("/git-credentials", headers={"Authorization": "Bearer bogus"})
    assert resp.status == 401


async def test_enroll_requires_admin_header(client):
    resp = await client.post("/admin/enroll", json={"sandbox_id": "s1", "sandbox_token": TOK_S1})
    assert resp.status == 401


async def test_enroll_with_wrong_admin_rejected(client):
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK_S1},
        headers={"X-Admin-Token": "wrong"},
    )
    assert resp.status == 401


async def test_enrolled_bearer_returns_credentials(client):
    enroll = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK_S1},
        headers={"X-Admin-Token": ADMIN},
    )
    assert enroll.status == 200

    resp = await client.get("/git-credentials", headers={"Authorization": f"Bearer {TOK_S1}"})
    assert resp.status == 200
    body = await resp.text()
    assert "username=x-access-token" in body
    assert PAT in body


async def test_multi_tenant_both_work(client):
    for sid, tok in [("s1", TOK_S1), ("s2", TOK_S2)]:
        await client.post(
            "/admin/enroll",
            json={"sandbox_id": sid, "sandbox_token": tok},
            headers={"X-Admin-Token": ADMIN},
        )
    for tok in (TOK_S1, TOK_S2):
        resp = await client.get("/git-credentials", headers={"Authorization": f"Bearer {tok}"})
        assert resp.status == 200


async def test_unenroll_revokes(client):
    await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK_S1},
        headers={"X-Admin-Token": ADMIN},
    )
    resp = await client.delete("/admin/enroll/s1", headers={"X-Admin-Token": ADMIN})
    assert resp.status == 200

    resp = await client.get("/git-credentials", headers={"Authorization": f"Bearer {TOK_S1}"})
    assert resp.status == 401


async def test_create_app_rejects_empty_admin():
    with pytest.raises(ValueError, match="admin_token is required"):
        create_git_proxy_app(github_token="x", admin_token="")


# --- I-1: re-enrollment test ---


async def test_re_enroll_replaces_old_token(client):
    """Enrolling s1 with token B replaces token A; A → 401, B → 200; unenroll removes both."""
    # Enroll with token A
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK_A},
        headers={"X-Admin-Token": ADMIN},
    )
    assert resp.status == 200

    # Token A should work
    resp = await client.get("/git-credentials", headers={"Authorization": f"Bearer {TOK_A}"})
    assert resp.status == 200

    # Re-enroll s1 with token B
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": TOK_B},
        headers={"X-Admin-Token": ADMIN},
    )
    assert resp.status == 200

    # Token A must now be rejected
    resp = await client.get("/git-credentials", headers={"Authorization": f"Bearer {TOK_A}"})
    assert resp.status == 401

    # Token B must work
    resp = await client.get("/git-credentials", headers={"Authorization": f"Bearer {TOK_B}"})
    assert resp.status == 200

    # Unenroll s1 — both dicts should be clean
    resp = await client.delete("/admin/enroll/s1", headers={"X-Admin-Token": ADMIN})
    assert resp.status == 200

    resp = await client.get("/git-credentials", headers={"Authorization": f"Bearer {TOK_B}"})
    assert resp.status == 401


# --- I-2: token length floor ---


async def test_enroll_rejects_short_sandbox_token(client):
    """sandbox_token shorter than 32 chars must be rejected with 400."""
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": "x"},
        headers={"X-Admin-Token": ADMIN},
    )
    assert resp.status == 400


# --- I-3: sandbox_id format validation ---


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
