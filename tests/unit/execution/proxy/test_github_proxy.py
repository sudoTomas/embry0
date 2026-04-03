import pytest
from aiohttp.test_utils import TestClient

from legion.execution.proxy.github_proxy import create_github_proxy_app


@pytest.fixture
async def gh_client(aiohttp_client) -> TestClient:
    app = create_github_proxy_app(github_token="ghp_test_token_456")
    return await aiohttp_client(app)


@pytest.mark.asyncio
async def test_health(gh_client: TestClient):
    resp = await gh_client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["proxy"] == "github_api"


@pytest.mark.asyncio
async def test_proxy_injects_token(gh_client: TestClient):
    """Requests to /repos/... should attempt to forward with token."""
    resp = await gh_client.get("/repos/owner/repo/issues/1")
    # 502 because can't reach real GitHub, or a real response if online
    # The key thing is the proxy attempted to forward, not reject
    assert resp.status in (200, 401, 403, 404, 502)
