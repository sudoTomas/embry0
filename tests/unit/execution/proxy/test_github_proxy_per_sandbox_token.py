import pytest
from aiohttp.test_utils import TestClient, TestServer

from embry0.execution.proxy.github_proxy import create_github_proxy_app

ADMIN = "admin-secret-xxxxxxxxxxxxxxxxxxxx"
GLOBAL = "ghp_GLOBAL"
TOK = "tok-" + "a" * 32


class _FakeResp:
    status_code = 200
    content = b"{}"
    headers = {"content-type": "application/json"}


class _FakeClient:
    def __init__(self):
        self.last_headers = None

    async def request(self, method, url, headers, content):
        self.last_headers = headers
        return _FakeResp()

    async def aclose(self):
        pass


@pytest.fixture
async def client_and_fake():
    app = create_github_proxy_app(github_token=GLOBAL, admin_token=ADMIN)
    fake = _FakeClient()
    async with TestClient(TestServer(app)) as c:
        app["http_client"] = fake  # override the real httpx client set on startup
        yield c, fake


async def _enroll(c, sid, tok, github_token=None):
    body = {"sandbox_id": sid, "sandbox_token": tok}
    if github_token:
        body["github_token"] = github_token
    return await c.post("/admin/enroll", json=body, headers={"X-Admin-Token": ADMIN})


async def test_per_sandbox_token_injected(client_and_fake):
    c, fake = client_and_fake
    await _enroll(c, "s1", TOK, github_token="ghp_RAVEN")
    await c.get("/user", headers={"Authorization": f"Bearer {TOK}"})
    assert fake.last_headers["Authorization"] == "Bearer ghp_RAVEN"


async def test_falls_back_to_global(client_and_fake):
    c, fake = client_and_fake
    await _enroll(c, "s1", TOK)
    await c.get("/user", headers={"Authorization": f"Bearer {TOK}"})
    assert fake.last_headers["Authorization"] == "Bearer ghp_GLOBAL"
