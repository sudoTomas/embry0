import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from legion.api.middleware.csrf import CSRFMiddleware


@pytest.fixture
def csrf_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CSRFMiddleware)

    @app.get("/test")
    async def get_test():
        return {"ok": True}

    @app.post("/test")
    async def post_test():
        return {"ok": True}

    @app.post("/webhook")
    async def webhook():
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_csrf_allows_get(csrf_app):
    transport = ASGITransport(app=csrf_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/test")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_csrf_blocks_post_without_header(csrf_app):
    transport = ASGITransport(app=csrf_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/test")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_csrf_allows_post_with_header(csrf_app):
    transport = ASGITransport(app=csrf_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/test", headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_csrf_exempts_webhook(csrf_app):
    transport = ASGITransport(app=csrf_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook")
    assert resp.status_code == 200
