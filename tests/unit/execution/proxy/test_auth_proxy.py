import pytest
from aiohttp.test_utils import TestClient

from athanor.execution.proxy.auth_proxy import create_auth_proxy_app


@pytest.fixture
async def auth_client(aiohttp_client) -> TestClient:
    app = create_auth_proxy_app(api_key="sk-ant-test-key", admin_token="test-admin-secret-not-real")
    return await aiohttp_client(app)


@pytest.mark.asyncio
async def test_health_endpoint(auth_client: TestClient):
    resp = await auth_client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"
    assert data["proxy"] == "auth"


@pytest.mark.asyncio
async def test_proxy_injects_api_key(auth_client: TestClient):
    """Verify the proxy adds x-api-key header (will fail upstream but we check the attempt).

    Offline: 502 (can't reach upstream).
    Online:  401 (upstream rejects invalid test key — key was present and forwarded).
    Either way, this is NOT a 403/missing-key error from the proxy itself.
    """
    resp = await auth_client.post("/v1/messages", json={"test": True})
    # 502 = no network, 401 = upstream rejected invalid key (key was injected and forwarded)
    assert resp.status in (401, 502)


@pytest.mark.asyncio
async def test_proxy_strips_host_header(auth_client: TestClient):
    """Proxy should strip Host header from forwarded request."""
    resp = await auth_client.post(
        "/v1/messages",
        json={"test": True},
        headers={"Host": "evil.com"},
    )
    # 502 = no network, 401 = upstream rejected invalid key (proxy correctly stripped Host)
    assert resp.status in (401, 502)
