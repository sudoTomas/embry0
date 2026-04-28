"""Auth proxy — injects Anthropic API key into Claude API requests.

Runs on sandbox-restricted network. Sandbox points ANTHROPIC_BASE_URL
at this proxy. The proxy validates the per-sandbox bearer token before
forwarding, then strips it and injects the real API key. Bearers are
enrolled by the orchestrator at sandbox-create time via /admin/enroll.
Credentials never enter the sandbox.
"""

import hashlib
import hmac
import json
import re

import httpx
import structlog
from aiohttp import web

logger = structlog.get_logger(__name__)

ANTHROPIC_API_BASE = "https://api.anthropic.com"

_SANDBOX_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def create_auth_proxy_app(api_key: str, admin_token: str) -> web.Application:
    """Create the auth proxy ASGI app.

    Args:
        api_key: Anthropic API key injected into forwarded requests.
        admin_token: Shared secret with orchestrator for /admin/enroll endpoints.
    """
    if not admin_token:
        raise ValueError("admin_token is required (PROXY_ADMIN_TOKEN must be set)")

    app = web.Application()
    app["api_key"] = api_key
    app["admin_token_hash"] = _sha256(admin_token)
    # in-memory only; ProxyManager.start() must re-enroll on proxy restart (Task 9 contract)
    # sandbox_id -> sha256(sandbox_token); used by unenroll to know what to remove
    app["enrolled_by_id"] = {}
    # sha256(sandbox_token) -> sandbox_id; used by proxy handler for O(1) lookup
    app["enrolled_by_hash"] = {}
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.router.add_get("/health", _health_handler)
    app.router.add_post("/admin/enroll", _enroll_handler)
    app.router.add_delete("/admin/enroll/{sandbox_id}", _unenroll_handler)
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


def _check_admin(request: web.Request) -> bool:
    presented = request.headers.get("X-Admin-Token", "")
    if not presented:
        logger.warning("auth_proxy_unauthorized", path=request.path)
        return False
    result = hmac.compare_digest(_sha256(presented), request.app["admin_token_hash"])
    if not result:
        logger.warning("auth_proxy_unauthorized", path=request.path)
    return result


def _check_bearer(request: web.Request) -> "str | None":
    """Return matched sandbox_id if bearer is valid, else None (and log warning)."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        logger.warning("auth_proxy_unauthorized", path=request.path)
        return None
    presented = auth[len("Bearer "):].strip()
    if not presented:
        logger.warning("auth_proxy_unauthorized", path=request.path)
        return None
    presented_hash = _sha256(presented)
    matched_sandbox = request.app["enrolled_by_hash"].get(presented_hash)
    if not matched_sandbox:
        logger.warning("auth_proxy_unauthorized", path=request.path)
        return None
    return matched_sandbox


async def _enroll_handler(request: web.Request) -> web.Response:
    if not _check_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "bad_request"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "bad_request"}, status=400)
    sandbox_id = body.get("sandbox_id", "")
    sandbox_token = body.get("sandbox_token", "")
    if not sandbox_id or not sandbox_token or len(sandbox_token) < 32:
        return web.json_response({"error": "bad_request"}, status=400)
    if not _SANDBOX_ID_RE.match(sandbox_id):
        return web.json_response({"error": "bad_request"}, status=400)
    # Handle re-enrollment: remove old hash entry if sandbox_id was already enrolled
    old_hash = request.app["enrolled_by_id"].get(sandbox_id)
    if old_hash is not None:
        request.app["enrolled_by_hash"].pop(old_hash, None)
    new_hash = _sha256(sandbox_token)
    request.app["enrolled_by_id"][sandbox_id] = new_hash
    request.app["enrolled_by_hash"][new_hash] = sandbox_id
    logger.info("auth_proxy_sandbox_enrolled", sandbox_id=sandbox_id)
    return web.json_response({"status": "enrolled"})


async def _unenroll_handler(request: web.Request) -> web.Response:
    if not _check_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    sandbox_id = request.match_info["sandbox_id"]
    if not _SANDBOX_ID_RE.match(sandbox_id):
        return web.json_response({"error": "bad_request"}, status=400)
    old_hash = request.app["enrolled_by_id"].pop(sandbox_id, None)
    if old_hash is not None:
        request.app["enrolled_by_hash"].pop(old_hash, None)
    logger.info("auth_proxy_sandbox_unenrolled", sandbox_id=sandbox_id)
    return web.json_response({"status": "unenrolled"})


async def _proxy_handler(request: web.Request) -> web.StreamResponse:
    """Forward request to Anthropic API with injected API key, after validating bearer."""
    sandbox_id = _check_bearer(request)
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

    body = await request.read()
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
