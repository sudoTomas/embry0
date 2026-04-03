"""Auth proxy — injects Anthropic API key into Claude API requests.

Runs on sandbox-restricted network. Sandbox points ANTHROPIC_BASE_URL
at this proxy. The proxy adds x-api-key header and forwards to the
real Anthropic API. Credentials never enter the sandbox.
"""

import httpx
import structlog
from aiohttp import web

logger = structlog.get_logger(__name__)

ANTHROPIC_API_BASE = "https://api.anthropic.com"


def create_auth_proxy_app(api_key: str) -> web.Application:
    """Create the auth proxy ASGI app."""
    app = web.Application()
    app["api_key"] = api_key
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.router.add_get("/health", _health_handler)
    app.router.add_route("*", "/v1/{path:.*}", _proxy_handler)
    return app


async def _on_startup(app: web.Application) -> None:
    app["http_client"] = httpx.AsyncClient(timeout=120.0)


async def _on_cleanup(app: web.Application) -> None:
    client = app.get("http_client")
    if client:
        await client.aclose()


async def _health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "proxy": "auth"})


async def _proxy_handler(request: web.Request) -> web.StreamResponse:
    """Forward request to Anthropic API with injected API key."""
    api_key = request.app["api_key"]
    path = "/" + request.match_info.get("path", "")
    upstream_url = f"{ANTHROPIC_API_BASE}/v1{path}"

    headers = dict(request.headers)
    headers.pop("Host", None)
    headers.pop("host", None)
    headers["x-api-key"] = api_key

    body = await request.read()
    client: httpx.AsyncClient = request.app["http_client"]

    try:
        async with client.stream(
            method=request.method,
            url=upstream_url,
            headers=headers,
            content=body,
        ) as upstream_resp:
            response = web.StreamResponse(status=upstream_resp.status_code)
            for hdr in ("content-type", "request-id"):
                if hdr in upstream_resp.headers:
                    response.headers[hdr] = upstream_resp.headers[hdr]
            await response.prepare(request)
            async for chunk in upstream_resp.aiter_bytes():
                await response.write(chunk)
            await response.write_eof()
            return response
    except httpx.ConnectError:
        return web.json_response(
            {"error": "Failed to connect to upstream API"},
            status=502,
        )
    except httpx.TimeoutException:
        return web.json_response(
            {"error": "Upstream API timeout"},
            status=504,
        )
