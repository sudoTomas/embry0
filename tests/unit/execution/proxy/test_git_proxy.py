import pytest
from aiohttp.test_utils import TestClient

from legion.execution.proxy.git_proxy import create_git_proxy_app


@pytest.fixture
async def git_client(aiohttp_client) -> TestClient:
    app = create_git_proxy_app(github_token="ghp_test_token_123")
    return await aiohttp_client(app)


@pytest.mark.asyncio
async def test_health(git_client: TestClient):
    resp = await git_client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["proxy"] == "git"


@pytest.mark.asyncio
async def test_git_credentials_endpoint(git_client: TestClient):
    """The /git-credentials endpoint returns token for git credential fill."""
    resp = await git_client.get("/git-credentials")
    assert resp.status == 200
    body = await resp.text()
    assert "x-access-token" in body
    assert "ghp_test_token_123" in body
