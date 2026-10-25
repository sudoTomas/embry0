"""Auth proxy — injects Anthropic API key into Claude API requests.

Runs on sandbox-restricted network. Sandbox points ANTHROPIC_BASE_URL
at this proxy. The proxy validates the per-sandbox bearer token before
forwarding, then strips it and injects the real API key. Bearers are
enrolled by the orchestrator at sandbox-create time via /admin/enroll
(shared hardened registry — TTL, cap, admin backoff — in
:mod:`embry0.execution.proxy.enrollment`, RAV-1070).
Credentials never enter the sandbox.
"""

import httpx
import structlog
from aiohttp import web

from embry0.execution.proxy.enrollment import check_bearer, register_admin_routes
from embry0.execution.proxy.model_request import DEFAULT_MAX_REQUEST_BYTES, read_body

logger = structlog.get_logger(__name__)

ANTHROPIC_API_BASE = "https://api.anthropic.com"


def create_auth_proxy_app(
    api_key: str, admin_token: str, *, max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
) -> web.Application:
    """Create the auth proxy ASGI app.

    Args:
        api_key: Anthropic API key injected into forwarded requests.
        admin_token: Shared secret with orchestrator for /admin/enroll endpoints.
        max_request_bytes: The largest request body it forwards (``model_request``).
    """
    if max_request_bytes <= 0:
        raise ValueError("MAX_REQUEST_BYTES must be positive")
    app = web.Application(client_max_size=max_request_bytes)
    app["api_key"] = api_key
    register_admin_routes(app, proxy_name="auth_proxy", admin_token=admin_token)
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
    """Forward request to Anthropic API with injected API key, after validating bearer."""
    sandbox_id = check_bearer(request, "auth_proxy")
    if sandbox_id is None:
        return web.json_response({"error": "unauthorized"}, status=401)

    api_key = request.app["api_key"]
    path = "/" + request.match_info.get("path", "")
    upstream_url = f"{ANTHROPIC_API_BASE}/v1{path}"

    headers = dict(request.headers)
    headers.pop("Host", None)
    headers.pop("host", None)
    # Strip sandbox bearer before injecting real API key
    headers.pop("Authorization", None)
    headers.pop("authorization", None)
    # Strip any sandbox-supplied x-api-key before injecting the real one
    headers.pop("x-api-key", None)
    headers.pop("X-Api-Key", None)  # case variants from dict() of CIMultiDict
    headers["x-api-key"] = api_key

    body = await read_body(request, "auth-proxy")
    if isinstance(body, web.Response):
        return body
    client: httpx.AsyncClient = request.app["http_client"]

    logger.info("auth_credentials_used", sandbox_id=sandbox_id)

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
            try:
                async for chunk in upstream_resp.aiter_bytes():
                    await response.write(chunk)
            except httpx.TransportError as exc:
                logger.warning("auth_proxy_stream_truncated", error=str(exc))
                # Cannot signal to client — response already prepared. Truncate gracefully.
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
