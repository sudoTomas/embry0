"""Local-model proxy: a sandbox's one way to the operator's own model server.

A model server on the operator's network (vLLM, Ollama, LM Studio) sits in the
private ranges the sandbox egress drop blocks. Declaring its host as an egress
exception opens every port of that machine to the sandbox, which is more than
a model call needs when the machine also runs other services. This proxy runs
on ``sandbox-restricted`` like the git and xAI proxies, and forwards to exactly
one upstream, the API root the operator configured (``LOCAL_LLM_UPSTREAM``):

- only ``POST /v1/chat/completions`` and ``GET /v1/models``; every other path
  is 404 and never reaches the upstream;
- only from an enrolled sandbox: the per-sandbox bearer is checked and
  stripped, and the upstream's own key (``LOCAL_LLM_API_KEY``, when the server
  checks one) is added here, so it never enters a sandbox;
- a request body over ``MAX_REQUEST_BYTES`` is refused with 413.

The orchestrator enrolls only the sandboxes of jobs that resolved the custom
provider, so a sandbox with no local model has no bearer this proxy accepts.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from aiohttp import web

from embry0.execution.proxy.enrollment import check_bearer, register_admin_routes

logger = structlog.get_logger(__name__)

PROXY_NAME = "local_llm_proxy"
DEFAULT_MAX_REQUEST_BYTES = 8 * 1024 * 1024
# A long completion from a model on one card can take minutes; the run's own
# timeout bounds it. Connect fails fast so an upstream that is down says so.
_TIMEOUT = httpx.Timeout(900.0, connect=10.0)
# The request headers forwarded upstream. Everything else, the sandbox's
# Authorization above all, stays behind.
_FORWARDED_REQUEST_HEADERS = ("content-type", "accept")
_FORWARDED_RESPONSE_HEADERS = ("content-type",)
# path under /v1 -> the one method allowed on it
_ROUTES: dict[str, str] = {"chat/completions": "POST", "models": "GET"}


def create_local_llm_proxy_app(
    upstream_url: str,
    admin_token: str,
    *,
    upstream_api_key: str = "",
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
) -> web.Application:
    """The proxy app forwarding to ``upstream_url``, an API root such as ``http://host:port/v1``."""
    if not upstream_url.startswith(("http://", "https://")):
        raise ValueError("UPSTREAM_URL must be an http(s) URL")
    if max_request_bytes <= 0:
        raise ValueError("MAX_REQUEST_BYTES must be positive")
    # One byte over the cap still reads, so the handler can answer 413 itself
    # instead of aiohttp's bare error.
    app = web.Application(client_max_size=max_request_bytes + 1)
    app["upstream_root"] = upstream_url.rstrip("/")
    app["upstream_api_key"] = upstream_api_key
    app["max_request_bytes"] = max_request_bytes
    register_admin_routes(app, proxy_name=PROXY_NAME, admin_token=admin_token)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.router.add_get("/health", _health_handler)
    app.router.add_route("*", "/v1/{path:.*}", _proxy_handler)
    return app


async def _on_startup(app: web.Application) -> None:
    app["http_client"] = httpx.AsyncClient(timeout=_TIMEOUT)


async def _on_cleanup(app: web.Application) -> None:
    client = app.get("http_client")
    if client:
        await client.aclose()


async def _health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "proxy": "local_llm"})


def _upstream_headers(request: web.Request) -> dict[str, str]:
    headers = {name: request.headers[name] for name in _FORWARDED_REQUEST_HEADERS if name in request.headers}
    key = request.app["upstream_api_key"]
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


async def _proxy_handler(request: web.Request) -> web.StreamResponse:
    path = request.match_info.get("path", "").strip("/")
    method = _ROUTES.get(path)
    if method is None:
        return web.json_response({"error": "not_found"}, status=404)
    if request.method != method:
        return web.json_response({"error": "method_not_allowed"}, status=405)
    sandbox_id = check_bearer(request, PROXY_NAME)
    if sandbox_id is None:
        return web.json_response({"error": "unauthorized"}, status=401)

    body = b""
    if method == "POST":
        limit: int = request.app["max_request_bytes"]
        if request.content_length is not None and request.content_length > limit:
            return _too_large(sandbox_id, request.content_length, limit)
        try:
            body = await request.read()
        except web.HTTPRequestEntityTooLarge:
            return _too_large(sandbox_id, None, limit)
        if len(body) > limit:
            return _too_large(sandbox_id, len(body), limit)

    client: httpx.AsyncClient = request.app["http_client"]
    url = f"{request.app['upstream_root']}/{path}"
    logger.info("local_llm_proxy_forward", sandbox_id=sandbox_id, path=path, bytes=len(body))
    try:
        async with client.stream(method, url, headers=_upstream_headers(request), content=body or None) as upstream:
            response = web.StreamResponse(status=upstream.status_code)
            for name in _FORWARDED_RESPONSE_HEADERS:
                if name in upstream.headers:
                    response.headers[name] = upstream.headers[name]
            await response.prepare(request)
            try:
                async for chunk in upstream.aiter_bytes():
                    await response.write(chunk)
            except httpx.TransportError as exc:
                logger.warning("local_llm_proxy_stream_truncated", sandbox_id=sandbox_id, error=str(exc))
            await response.write_eof()
            return response
    except httpx.ConnectError:
        return web.json_response({"error": "the local model server is unreachable"}, status=502)
    except httpx.TimeoutException:
        return web.json_response({"error": "the local model server timed out"}, status=504)


def _too_large(sandbox_id: str, size: Any, limit: int) -> web.Response:
    logger.warning("local_llm_proxy_request_too_large", sandbox_id=sandbox_id, size=size, limit=limit)
    return web.json_response(
        {"error": {"message": f"request body over the local model proxy's limit of {limit} bytes"}}, status=413
    )
