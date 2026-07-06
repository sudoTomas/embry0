import pytest
from aiohttp.test_utils import TestClient, TestServer

from athanor.execution.proxy.git_proxy import create_git_proxy_app

ADMIN = "admin-secret-xxxxxxxxxxxxxxxxxxxx"
GLOBAL = "ghp_GLOBAL"
TOK = "tok-" + "a" * 32


@pytest.fixture
async def client():
    app = create_git_proxy_app(github_token=GLOBAL, admin_token=ADMIN)
    async with TestClient(TestServer(app)) as c:
        yield c


async def _enroll(client, sid, tok, github_token=None):
    body = {"sandbox_id": sid, "sandbox_token": tok}
    if github_token:
        body["github_token"] = github_token
    return await client.post("/admin/enroll", json=body, headers={"X-Admin-Token": ADMIN})


async def test_per_sandbox_token_returned(client):
    await _enroll(client, "s1", TOK, github_token="ghp_RAVEN")
    r = await client.get("/git-credentials", headers={"Authorization": f"Bearer {TOK}"})
    body = await r.text()
    assert "password=ghp_RAVEN" in body
    assert "ghp_GLOBAL" not in body


async def test_falls_back_to_global_without_per_sandbox_token(client):
    await _enroll(client, "s1", TOK)
    r = await client.get("/git-credentials", headers={"Authorization": f"Bearer {TOK}"})
    assert "password=ghp_GLOBAL" in await r.text()


async def test_two_sandboxes_get_their_own_tokens(client):
    t1, t2 = "tok-1-" + "a" * 32, "tok-2-" + "b" * 32
    await _enroll(client, "s1", t1, github_token="ghp_A")
    await _enroll(client, "s2", t2, github_token="ghp_B")
    r1 = await client.get("/git-credentials", headers={"Authorization": f"Bearer {t1}"})
    r2 = await client.get("/git-credentials", headers={"Authorization": f"Bearer {t2}"})
    assert "password=ghp_A" in await r1.text()
    assert "password=ghp_B" in await r2.text()


async def test_unenroll_clears_per_sandbox_token(client):
    await _enroll(client, "s1", TOK, github_token="ghp_RAVEN")
    await client.delete("/admin/enroll/s1", headers={"X-Admin-Token": ADMIN})
    r = await client.get("/git-credentials", headers={"Authorization": f"Bearer {TOK}"})
    assert r.status == 401
