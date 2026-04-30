import pytest
from aiohttp.test_utils import TestClient

from athanor.execution.proxy.git_proxy import create_git_proxy_app


@pytest.fixture
async def git_client(aiohttp_client) -> TestClient:
    app = create_git_proxy_app(github_token="ghp_test_token_123", admin_token="test-admin")
    return await aiohttp_client(app)


@pytest.mark.asyncio
async def test_health(git_client: TestClient):
    resp = await git_client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["proxy"] == "git"


@pytest.mark.asyncio
async def test_git_credentials_endpoint(git_client: TestClient):
    """The /git-credentials endpoint requires a valid bearer; enroll one first."""
    # Enroll a sandbox token
    token = "tok-legacy-" + "x" * 32  # must be >= 32 chars
    enroll = await git_client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": token},
        headers={"X-Admin-Token": "test-admin"},
    )
    assert enroll.status == 200

    resp = await git_client.get("/git-credentials", headers={"Authorization": f"Bearer {token}"})
    assert resp.status == 200
    body = await resp.text()
    assert "x-access-token" in body
    assert "ghp_test_token_123" in body
