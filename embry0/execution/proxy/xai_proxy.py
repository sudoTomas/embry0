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
``/admin/enroll`` (shared hardened registry — TTL, cap, admin backoff — in
:mod:`embry0.execution.proxy.enrollment`, RAV-1070).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import structlog
from aiohttp import web

from embry0.execution.proxy.enrollment import EnrollRejected, check_bearer, guard_admin, register_admin_routes
from embry0.execution.proxy.model_request import DEFAULT_MAX_REQUEST_BYTES, read_body
from embry0.safety.xai_denials import XaiDenial, classify_xai_denial

logger = structlog.get_logger(__name__)

XAI_API_BASE = "https://api.x.ai"


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


class _XaiCredentialEnroll:
    """Remember which credential a sandbox enrolled, when the body names one.

    Absent or empty means the sandbox uses the global token (an API-key job,
    or the file-backed refresher). A non-string is refused.
    """

    def parse(self, app: web.Application, body: dict[str, Any]) -> str | None:
        credential_id = body.get("xai_credential_id")
        if credential_id is None or credential_id == "":
            return None
        if not isinstance(credential_id, str):
            raise EnrollRejected("xai_credential_id must be a string")
        return credential_id

    def apply(self, app: web.Application, sandbox_id: str, parsed: str | None) -> dict[str, Any]:
        if parsed:
            app["xai_credential_by_sandbox"][sandbox_id] = parsed
        else:
            app["xai_credential_by_sandbox"].pop(sandbox_id, None)
        return {}


def _forget_xai_credential(app: web.Application, sandbox_id: str) -> None:
    app["xai_credential_by_sandbox"].pop(sandbox_id, None)


def create_xai_proxy_app(admin_token: str, *, max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES) -> web.Application:
    """Create the xAI proxy ASGI app.

    Args:
        admin_token: Shared secret with the orchestrator for the ``/admin/*`` endpoints.

    The access token is NOT passed here — it is pushed at runtime via ``/admin/token`` and
    refreshed by the orchestrator. The proxy starts un-provisioned and returns 503 until the
    first token push. ``max_request_bytes`` is the largest request body it forwards
    (``model_request``): a long agent conversation is megabytes of JSON.
    """
    if max_request_bytes <= 0:
        raise ValueError("MAX_REQUEST_BYTES must be positive")
    app = web.Application(client_max_size=max_request_bytes)
    # Current SuperGrok access token, pushed by the orchestrator. Held in a mutable dict
    # (not a bare app-mapping key) so it can be updated after startup without aiohttp's
    # "changing state of a started application" deprecation. Empty until provisioned.
    app["token_holder"] = {"access_token": ""}
    # Per-credential access tokens. A sandbox enrolled with xai_credential_id
    # reads its slot; the global holder stays for enrollments that name none.
    app["tokens"] = {}
    app["xai_credential_by_sandbox"] = {}
    app["upstream_counts"] = {}
    register_admin_routes(
        app,
        proxy_name="xai_proxy",
        admin_token=admin_token,
        enroll_extension=_XaiCredentialEnroll(),
        on_unenroll=_forget_xai_credential,
    )
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.router.add_get("/health", _health_handler)
    app.router.add_get("/admin/health", _admin_health_handler)
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


async def _answer_403(request: web.Request, upstream_resp: Any, credential_id: str | None) -> web.Response:
    """Pass an xAI 403 through whole, counting a stale-token one for its account.

    xAI says what a 403 means only in its body (embry0/safety/xai_denials.py), and an
    error body is small JSON, so it is read in full rather than streamed. A stale-token
    403 is counted under ``stale`` so the orchestrator's token loop force-refreshes that
    account; the other conditions reach the job through the executor's error.
    """
    body = await upstream_resp.aread()
    denial = classify_xai_denial(body.decode("utf-8", errors="replace"))
    logger.warning("xai_proxy_upstream_403", credential_id=credential_id, denial=denial.value if denial else None)
    if credential_id and denial is XaiDenial.STALE_TOKEN:
        bucket = request.app["upstream_counts"].setdefault(credential_id, {"401": 0, "429": 0})
        bucket["stale"] = bucket.get("stale", 0) + 1
    headers = {
        hdr: upstream_resp.headers[hdr] for hdr in ("content-type", "request-id") if hdr in upstream_resp.headers
    }
    return web.Response(status=403, body=body, headers=headers)


async def _admin_health_handler(request: web.Request) -> web.Response:
    """Per-credential upstream 401/429 counts since the previous read. Reading resets them."""
    denied = guard_admin(request, "xai_proxy")
    if denied is not None:
        return denied
    # Copy then clear. Replacing the app key after startup is the deprecation
    # the token holder avoids by mutating a dict in place.
    counts = {key: dict(bucket) for key, bucket in request.app["upstream_counts"].items()}
    request.app["upstream_counts"].clear()
    return web.json_response({"credentials": counts})


async def _set_token_handler(request: web.Request) -> web.Response:
    """Orchestrator pushes a SuperGrok access token (admin-gated).

    A body with ``credential_id`` fills that credential's slot. A body without
    one fills the global holder the file-backed refresher still uses.
    """
    denied = guard_admin(request, "xai_proxy")
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "bad_request"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "bad_request"}, status=400)
    access_token = body.get("access_token", "")
    if not access_token or not isinstance(access_token, str):
        return web.json_response({"error": "bad_request"}, status=400)
    credential_id = body.get("credential_id")
    if credential_id:
        if not isinstance(credential_id, str):
            return web.json_response({"error": "bad_request"}, status=400)
        request.app["tokens"][credential_id] = {
            "access_token": access_token,
            "expires_at": body.get("expires_at"),
        }
        logger.info("xai_proxy_credential_token_set", credential_id=credential_id)
    else:
        request.app["token_holder"]["access_token"] = access_token
        logger.info("xai_proxy_token_set")
    return web.json_response({"status": "ok"})


async def _proxy_handler(request: web.Request) -> web.StreamResponse:
    """Forward to api.x.ai with the injected SuperGrok bearer, after validating the sandbox bearer."""
    sandbox_id = check_bearer(request, "xai_proxy")
    if sandbox_id is None:
        return web.json_response({"error": "unauthorized"}, status=401)

    credential_id = request.app["xai_credential_by_sandbox"].get(sandbox_id)
    if credential_id:
        slot = request.app["tokens"].get(credential_id) or {}
        access_token = slot.get("access_token") or ""
        if not access_token:
            logger.warning("xai_proxy_credential_token_missing", sandbox_id=sandbox_id, credential_id=credential_id)
            return web.json_response({"error": "credential_token_missing"}, status=503)
    else:
        credential_id = None
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

    body = await read_body(request, "xai-proxy")
    if isinstance(body, web.Response):
        return body
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
            if credential_id and upstream_resp.status_code in (401, 429):
                bucket = request.app["upstream_counts"].setdefault(credential_id, {"401": 0, "429": 0})
                bucket[str(upstream_resp.status_code)] += 1
            if upstream_resp.status_code == 403:
                return await _answer_403(request, upstream_resp, credential_id)
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
