"""Tests for git_proxy bearer auth + admin enrollment."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from athanor.execution.proxy.git_proxy import create_git_proxy_app

ADMIN = "test-admin-secret-not-real"
PAT = "ghp_PAT_TESTING_ONLY"


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
    resp = await client.post("/admin/enroll", json={"sandbox_id": "s1", "sandbox_token": "t1"})
    assert resp.status == 401


async def test_enroll_with_wrong_admin_rejected(client):
    resp = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": "t1"},
        headers={"X-Admin-Token": "wrong"},
    )
    assert resp.status == 401


async def test_enrolled_bearer_returns_credentials(client):
    enroll = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": "tok-s1"},
        headers={"X-Admin-Token": ADMIN},
    )
    assert enroll.status == 200

    resp = await client.get(
        "/git-credentials", headers={"Authorization": "Bearer tok-s1"}
    )
    assert resp.status == 200
    body = await resp.text()
    assert "username=x-access-token" in body
    assert PAT in body


async def test_multi_tenant_both_work(client):
    for sid, tok in [("s1", "tok-s1"), ("s2", "tok-s2")]:
        await client.post(
            "/admin/enroll",
            json={"sandbox_id": sid, "sandbox_token": tok},
            headers={"X-Admin-Token": ADMIN},
        )
    for tok in ("tok-s1", "tok-s2"):
        resp = await client.get(
            "/git-credentials", headers={"Authorization": f"Bearer {tok}"}
        )
        assert resp.status == 200


async def test_unenroll_revokes(client):
    await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": "tok-s1"},
        headers={"X-Admin-Token": ADMIN},
    )
    resp = await client.delete("/admin/enroll/s1", headers={"X-Admin-Token": ADMIN})
    assert resp.status == 200

    resp = await client.get(
        "/git-credentials", headers={"Authorization": "Bearer tok-s1"}
    )
    assert resp.status == 401


async def test_create_app_rejects_empty_admin():
    import pytest as _pytest

    with _pytest.raises(ValueError, match="admin_token is required"):
        create_git_proxy_app(github_token="x", admin_token="")
