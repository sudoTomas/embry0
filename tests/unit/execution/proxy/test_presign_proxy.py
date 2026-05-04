"""Tests for the presign endpoint reverse proxy."""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from athanor.execution.proxy.presign_proxy import create_presign_proxy_app


@pytest.mark.asyncio
async def test_health_returns_ok(aiohttp_client):
    app = create_presign_proxy_app("http://example.invalid:8000")
    client: TestClient = await aiohttp_client(app)
    resp = await client.get("/health")
    assert resp.status == 200
    assert (await resp.text()) == "ok"


def test_create_requires_upstream_url():
    with pytest.raises(ValueError, match="UPSTREAM_URL is required"):
        create_presign_proxy_app("")


@pytest.mark.asyncio
async def test_only_presign_path_is_proxied(aiohttp_client):
    """Random paths return 404 — the presign proxy is purpose-built for one route.

    The credential-injection proxies follow the same pattern; we want a
    similarly narrow attack surface here.
    """
    app = create_presign_proxy_app("http://example.invalid:8000")
    client: TestClient = await aiohttp_client(app)
    resp = await client.post("/some/other/path")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_proxy_forwards_body_and_returns_response(aiohttp_client):
    seen: dict[str, object] = {}

    async def upstream_handler(request: web.Request) -> web.Response:
        seen["method"] = request.method
        seen["path"] = request.path
        seen["body"] = await request.json()
        return web.json_response({"bucket": "qa-artifacts", "prefix": "j1/1/", "urls": []}, status=200)

    upstream_app = web.Application()
    upstream_app.router.add_post("/api/v1/internal/qa/presign", upstream_handler)
    upstream_server = TestServer(upstream_app)
    await upstream_server.start_server()
    try:
        proxy_app = create_presign_proxy_app(f"http://127.0.0.1:{upstream_server.port}")
        client: TestClient = await aiohttp_client(proxy_app)

        body = {"sandbox_token": "tok", "paths": ["x.json"]}
        resp = await client.post(
            "/api/v1/internal/qa/presign",
            data=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )

        assert resp.status == 200
        data = await resp.json()
        assert data == {"bucket": "qa-artifacts", "prefix": "j1/1/", "urls": []}
        assert seen["method"] == "POST"
        assert seen["path"] == "/api/v1/internal/qa/presign"
        assert seen["body"] == body
    finally:
        await upstream_server.close()


@pytest.mark.asyncio
async def test_proxy_returns_502_on_upstream_unreachable(aiohttp_client):
    proxy_app = create_presign_proxy_app("http://127.0.0.1:1")
    client: TestClient = await aiohttp_client(proxy_app)
    resp = await client.post(
        "/api/v1/internal/qa/presign",
        data='{"sandbox_token": "x", "paths": []}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 502
    assert "Upstream error" in await resp.text()
