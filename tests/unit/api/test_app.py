import pytest
from httpx import ASGITransport, AsyncClient

try:
    from legion.api.app import create_app
except ImportError:
    pytest.skip("psycopg not available", allow_module_level=True)


@pytest.mark.asyncio
async def test_app_creates_successfully():
    app = create_app()
    assert app is not None
    assert app.title == "Legion API"


@pytest.mark.asyncio
async def test_health_endpoint():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
