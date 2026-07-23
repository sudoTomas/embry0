"""xAI proxy — injects the SuperGrok OAuth bearer into api.x.ai requests (EMB-45).

Runs on the sandbox-restricted network. The direct-xAI executor inside a sandbox points its
Anthropic-SDK ``base_url`` at this proxy. The proxy validates the per-sandbox bearer, strips
it, and injects the current SuperGrok **access token** as ``Authorization: Bearer`` before
forwarding to ``https://api.x.ai``. The SuperGrok credential never enters the sandbox.

Unlike auth-proxy (which holds a static Anthropic API key), the injected access token is
short-lived and rotates. The orchestrator owns the rotating refresh-token lineage
(:mod:`embry0.execution.xai_credential`) and pushes the current access token here via the
admin-gated ``POST /admin/token`` endpoint — the same in-DinD ``docker exec`` transport used
for ``/admin/enroll``. This keeps the proxy stateless (re-provisioned on restart) and makes
the orchestrator the single serialized owner of the rotating refresh token.

Per-sandbox bearers are enrolled by the orchestrator at sandbox-create time via
``/admin/enroll`` (identical contract to auth-proxy/git-proxy).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re

import httpx
import structlog
from aiohttp import web

logger = structlog.get_logger(__name__)

XAI_API_BASE = "https://api.x.ai"

_SANDBOX_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _normalize_tool_schemas(body: bytes) -> bytes:
    """Make outbound tool defs acceptable to xAI's stricter schema validator.

    xAI's Anthropic-compat endpoint rejects any tool whose ``input_schema``
    omits the ``required`` key ("/required: null is not of type 'array'").
    Anthropic-family clients legitimately omit it for all-optional tools —
    including the Claude Code CLI's own builtin tools, whose schemas embry0
    cannot patch — so the proxy normalizes every outbound request at the one
    choke point all grok traffic shares (EMB-46; sibling of the client-side
    fix in agents/mcp_client.anthropic_tool_defs, #35).

    Returns the original bytes unless a fix was actually needed; any parse
    failure forwards the body untouched.
    """
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return body
    if not isinstance(payload, dict):
        return body
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return body
    changed = False
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        schema = tool.get("input_schema")
        if isinstance(schema, dict) and not isinstance(schema.get("required"), list):
            schema["required"] = []
            changed = True
    if not changed:
        return body
    return json.dumps(payload).encode("utf-8")


def create_xai_proxy_app(admin_token: str) -> web.Application:
    """Create the xAI proxy ASGI app.

    Args:
        admin_token: Shared secret with the orchestrator for the ``/admin/*`` endpoints.

    The access token is NOT passed here — it is pushed at runtime via ``/admin/token`` and
    refreshed by the orchestrator. The proxy starts un-provisioned and returns 503 until the
    first token push.
    """
    if not admin_token:
        raise ValueError("admin_token is required (PROXY_ADMIN_TOKEN must be set)")

    app = web.Application()
    app["admin_token_hash"] = _sha256(admin_token)
    # Current SuperGrok access token, pushed by the orchestrator. Held in a mutable dict
    # (not a bare app-mapping key) so it can be updated after startup without aiohttp's
    # "changing state of a started application" deprecation. Empty until provisioned.
    app["token_holder"] = {"access_token": ""}
    # in-memory only; ProxyManager.start() must re-enroll + re-push on proxy restart.
    app["enrolled_by_id"] = {}
    app["enrolled_by_hash"] = {}
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.router.add_get("/health", _health_handler)
    app.router.add_post("/admin/enroll", _enroll_handler)
    app.router.add_delete("/admin/enroll/{sandbox_id}", _unenroll_handler)
    app.router.add_post("/admin/token", _set_token_handler)
    app.router.add_route("*", "/v1/{path:.*}", _proxy_handler)
    return app


async def _on_startup(app: web.Application) -> None:
    app["http_client"] = httpx.AsyncClient(timeout=120.0)


async def _on_cleanup(app: web.Application) -> None:
    client = app.get("http_client")
    if client:
        await client.aclose()


async def _health_handler(request: web.Request) -> web.Response:
    provisioned = bool(request.app["token_holder"]["access_token"])
    return web.json_response({"status": "ok", "proxy": "xai", "provisioned": provisioned})


def _check_admin(request: web.Request) -> bool:
    presented = request.headers.get("X-Admin-Token", "")
    if not presented:
        logger.warning("xai_proxy_unauthorized", path=request.path)
        return False
    result = hmac.compare_digest(_sha256(presented), request.app["admin_token_hash"])
    if not result:
        logger.warning("xai_proxy_unauthorized", path=request.path)
    return result


def _check_bearer(request: web.Request) -> str | None:
    """Return matched sandbox_id if bearer is valid, else None (and log warning)."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        logger.warning("xai_proxy_unauthorized", path=request.path)
        return None
    presented = auth[len("Bearer ") :].strip()
    if not presented:
        logger.warning("xai_proxy_unauthorized", path=request.path)
        return None
    raw_match = request.app["enrolled_by_hash"].get(_sha256(presented))
    if not raw_match:
        logger.warning("xai_proxy_unauthorized", path=request.path)
        return None
    return str(raw_match)


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
    old_hash = request.app["enrolled_by_id"].get(sandbox_id)
    if old_hash is not None:
        request.app["enrolled_by_hash"].pop(old_hash, None)
    new_hash = _sha256(sandbox_token)
    request.app["enrolled_by_id"][sandbox_id] = new_hash
    request.app["enrolled_by_hash"][new_hash] = sandbox_id
    logger.info("xai_proxy_sandbox_enrolled", sandbox_id=sandbox_id)
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
    logger.info("xai_proxy_sandbox_unenrolled", sandbox_id=sandbox_id)
    return web.json_response({"status": "unenrolled"})


async def _set_token_handler(request: web.Request) -> web.Response:
    """Orchestrator pushes the current SuperGrok access token (admin-gated)."""
    if not _check_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "bad_request"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "bad_request"}, status=400)
    access_token = body.get("access_token", "")
    if not access_token or not isinstance(access_token, str):
        return web.json_response({"error": "bad_request"}, status=400)
    request.app["token_holder"]["access_token"] = access_token
    logger.info("xai_proxy_token_set")
    return web.json_response({"status": "ok"})


async def _proxy_handler(request: web.Request) -> web.StreamResponse:
    """Forward to api.x.ai with the injected SuperGrok bearer, after validating the sandbox bearer."""
    sandbox_id = _check_bearer(request)
    if sandbox_id is None:
        return web.json_response({"error": "unauthorized"}, status=401)

    access_token = request.app["token_holder"]["access_token"]
    if not access_token:
        logger.warning("xai_proxy_not_provisioned", sandbox_id=sandbox_id)
        return web.json_response({"error": "xai proxy not yet provisioned"}, status=503)

    path = "/" + request.match_info.get("path", "")
    upstream_url = f"{XAI_API_BASE}/v1{path}"

    headers = dict(request.headers)
    headers.pop("Host", None)
    headers.pop("host", None)
    # Strip the sandbox bearer and any sandbox-supplied credential headers before injecting.
    headers.pop("Authorization", None)
    headers.pop("authorization", None)
    headers.pop("x-api-key", None)
    headers.pop("X-Api-Key", None)
    headers["Authorization"] = f"Bearer {access_token}"

    # EMB-54: xAI prompt caching depends on sticky per-server routing via the
    # x-grok-conv-id header (cache entries are per-server; without it,
    # consecutive requests land on different servers and miss cache even with
    # identical prefixes). Nothing upstream sends it on the Anthropic-compat
    # path — the suspected cause of the weak caching discount on grok runs.
    # The enrolled sandbox identity is constant across a job's whole agent
    # session, which is exactly the granularity caching wants. A caller that
    # sets its own conv-id wins; the header is harmless if upstream ignores it.
    if "x-grok-conv-id" not in headers and "X-Grok-Conv-Id" not in headers:
        headers["x-grok-conv-id"] = sandbox_id

    body = await request.read()
    if body:
        normalized = _normalize_tool_schemas(body)
        if normalized is not body:
            body = normalized
            # Length changed — let httpx recompute from the new content.
            headers.pop("Content-Length", None)
            headers.pop("content-length", None)
    client: httpx.AsyncClient = request.app["http_client"]

    logger.info("xai_credentials_used", sandbox_id=sandbox_id)

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
                logger.warning("xai_proxy_stream_truncated", error=str(exc))
            await response.write_eof()
            return response
    except httpx.ConnectError:
        return web.json_response({"error": "Failed to connect to upstream API"}, status=502)
    except httpx.TimeoutException:
        return web.json_response({"error": "Upstream API timeout"}, status=504)
