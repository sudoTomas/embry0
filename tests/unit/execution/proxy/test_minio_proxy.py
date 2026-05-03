"""Tests for the MinIO transparent reverse proxy."""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from athanor.execution.proxy.minio_proxy import create_minio_proxy_app


@pytest.mark.asyncio
async def test_health_returns_ok(aiohttp_client):
    app = create_minio_proxy_app("http://example.invalid:9000")
    client: TestClient = await aiohttp_client(app)
    resp = await client.get("/health")
    assert resp.status == 200
    assert (await resp.text()) == "ok"


def test_create_requires_upstream_url():
    with pytest.raises(ValueError, match="UPSTREAM_URL is required"):
        create_minio_proxy_app("")


@pytest.mark.asyncio
async def test_proxy_forwards_method_and_body_and_query(aiohttp_client):
    """Spin a fake upstream server, point the proxy at it, and verify
    every HTTP attribute survives the hop intact."""
    seen: dict[str, object] = {}

    async def upstream_handler(request: web.Request) -> web.Response:
        seen["method"] = request.method
        seen["path"] = request.path
        seen["query"] = dict(request.rel_url.query)
        seen["body"] = await request.read()
        seen["custom_header"] = request.headers.get("X-Custom")
        return web.Response(
            status=201,
            text="upstream-body",
            headers={"X-Upstream-Header": "yes", "Content-Type": "text/plain"},
        )

    upstream_app = web.Application()
    upstream_app.router.add_route("*", "/{path:.*}", upstream_handler)
    upstream_server = TestServer(upstream_app)
    await upstream_server.start_server()
    try:
        upstream_url = f"http://127.0.0.1:{upstream_server.port}"
        proxy_app = create_minio_proxy_app(upstream_url)
        client: TestClient = await aiohttp_client(proxy_app)

        resp = await client.put(
            "/qa-artifacts/job1/1/foo.bin?X-Amz-Signature=abc&X-Amz-Date=20260101",
            data=b"hello-world",
            headers={"X-Custom": "preserved"},
        )

        assert resp.status == 201
        assert (await resp.text()) == "upstream-body"
        assert resp.headers["X-Upstream-Header"] == "yes"

        assert seen["method"] == "PUT"
        assert seen["path"] == "/qa-artifacts/job1/1/foo.bin"
        assert seen["query"] == {"X-Amz-Signature": "abc", "X-Amz-Date": "20260101"}
        assert seen["body"] == b"hello-world"
        assert seen["custom_header"] == "preserved"
    finally:
        await upstream_server.close()


@pytest.mark.asyncio
async def test_proxy_returns_502_on_upstream_unreachable(aiohttp_client):
    # Bind to an unreachable port; aiohttp ClientError → 502.
    proxy_app = create_minio_proxy_app("http://127.0.0.1:1")
    client: TestClient = await aiohttp_client(proxy_app)
    resp = await client.get("/anything")
    assert resp.status == 502
    assert "Upstream error" in await resp.text()
