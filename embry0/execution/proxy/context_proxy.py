"""Context proxy — mediated read-only log queries for the watcher's analysis agent (RAV-1140).

Runs on the sandbox-restricted network. The watcher's sandboxed analysis agent gets its
log window chosen blind; real diagnosis is iterative (widen around a timestamp, check the
sibling container, look before/after a deploy marker). This proxy gives that agent a
drill-down path to the orchestrator's Loki proxy (``GET /api/v1/logs/query``) without ever
exposing Loki itself — Loki is unauthenticated and carries every container's logs.

Mediation, all server-side:
- per-sandbox bearer enrollment (shared hardened registry — TTL, cap, admin backoff — in
  :mod:`embry0.execution.proxy.enrollment`, RAV-1070); enrollment is per-job opt-in —
  ProxyManager deliberately does NOT include this proxy in the ``enroll_sandbox``
  broadcast, only watcher-flagged jobs are enrolled via ``enroll_context()``.
- container-name allowlist (exact match, case-insensitive) — no sandbox-supplied free-form
  string ever reaches Loki's regex selector; a miss returns the allowed names. An entry
  may be a glob pattern (``shop-api*``) for containers named per release, such as
  blue/green slots; a name admitted by a pattern must be a plain container name
  (letters, digits, ``_``, ``-``), so it carries no regex syntax either
  (:mod:`embry0.execution.proxy.container_allowlist`).
- the raw ``logql`` override (and any unknown param) is rejected, never forwarded.
- numeric caps are clamped rather than rejected (the upstream endpoint 422s on
  out-of-range, and the agent must never see an opaque validation error).
- a per-sandbox lifetime query quota. In-memory, like the enrollment maps: both reset
  together on proxy restart, and the quota (with the scope below) ends with its
  enrollment, whether revoked, expired or replaced (#536).
- an optional per-sandbox container SCOPE (SH-P4.4): a developer job working a healing
  case is enrolled with ``allowed_containers`` — the case's services — and can query only
  those. The scope can narrow the global allowlist, never widen it, and a scope that grants
  nothing refuses the enrollment (it must not fall back to the full allowlist).
- every result is redacted line by line before it leaves the proxy (#294), scoped or not:
  the watcher's analysis agent reads the same production lines the developer does, and
  both hand them to a model provider. A 200 body the proxy cannot parse is withheld.

The proxy holds the orchestrator API key (auth-proxy precedent) and injects it upstream;
the key never enters the sandbox.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import structlog
from aiohttp import web

from embry0.execution.proxy.container_allowlist import ContainerAllowlist
from embry0.execution.proxy.enrollment import EnrollRejected, check_bearer, register_admin_routes
from embry0.safety.redaction import redact_bounded, redact_log_text, secret_env_values

logger = structlog.get_logger(__name__)

# Clamp bounds for forwarded query params. Tighter than the upstream endpoint's
# validators (since_minutes <= 1440, limit <= 1000): the watcher's investigation
# horizon is the tick window plus recent deploys, and the analysis agent reads
# results into a 40-turn budget (RAV-1055) — a 4h/200-line ceiling is generous.
MAX_SINCE_MINUTES = 240
MAX_LIMIT = 200
MAX_SEARCH_LEN = 500

_ALLOWED_PARAMS = {"container", "search", "since_minutes", "limit"}

_UPSTREAM_PATH = "/api/v1/logs/query"

_SCOPE_FIELD = "allowed_containers"

# Per-line ceiling applied BEFORE redaction. This handler is shared by every
# enrolled sandbox in one event loop, so the cost of redacting must not depend
# on the length of a line the log backend happened to return — one 20 KB
# single-token line (base64, an OCR payload, a logged JWT) would stall every
# other sandbox's query. Log lines are data, not executable text, so cutting
# one is acceptable; the marker says it happened.
MAX_REDACTED_LINE_CHARS = 2000


class _ContainerScope:
    """Enroll-body ``allowed_containers`` → a per-sandbox subset of the allowlist."""

    def parse(self, app: web.Application, body: dict[str, Any]) -> list[str] | None:
        if _SCOPE_FIELD not in body:
            return None  # unscoped: the full allowlist (watcher analysis jobs)
        requested = body[_SCOPE_FIELD]
        if not isinstance(requested, list) or not all(isinstance(c, str) for c in requested):
            raise EnrollRejected(f"{_SCOPE_FIELD} must be a list of container names")
        allowlist: ContainerAllowlist = app["allowlist"]
        # A pattern narrows to the concrete names asked for; it is never granted as itself.
        scope = sorted({name for c in requested if (name := allowlist.resolve(c)) is not None})
        if not scope:
            raise EnrollRejected(f"{_SCOPE_FIELD} names no queryable container")
        return scope

    def apply(self, app: web.Application, sandbox_id: str, parsed: list[str] | None) -> dict[str, Any]:
        scopes: dict[str, list[str]] = app["scope_by_id"]
        if parsed is None:
            scopes.pop(sandbox_id, None)
            return {}
        scopes[sandbox_id] = parsed
        return {_SCOPE_FIELD: parsed}


def _redacted_body(content: bytes) -> bytes | None:
    """The upstream payload with every log line redacted, or None if unreadable."""
    try:
        payload = json.loads(content)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    lines = payload.get("lines")
    if isinstance(lines, list):
        # The proxy's own secrets (its admin token, the orchestrator key) are
        # read once per body, not once per line.
        known = secret_env_values()

        def redact(text: str) -> str:
            return redact_log_text(text, known)

        for entry in lines:
            if isinstance(entry, dict) and isinstance(entry.get("line"), str):
                raw = entry["line"]
                entry["line"] = redact_bounded(raw, MAX_REDACTED_LINE_CHARS, redact=redact) + (
                    " … [line truncated]" if len(raw) > MAX_REDACTED_LINE_CHARS else ""
                )
    return json.dumps(payload).encode()


def _clamp(value: str | None, default: int, lo: int, hi: int) -> int:
    """Parse an int param, falling back to default on garbage, clamped to [lo, hi]."""
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(lo, min(hi, parsed))


def create_context_proxy_app(
    upstream_url: str,
    orchestrator_api_key: str,
    admin_token: str,
    container_allowlist: list[str],
    query_quota: int,
) -> web.Application:
    """Create the context proxy app.

    Args:
        upstream_url: orchestrator base URL (IP-resolved by ProxyManager — DinD DNS
            cannot resolve host service names).
        orchestrator_api_key: injected as ``Authorization: Bearer`` on the upstream
            call. May be empty in dev mode (upstream auth is skipped there too).
        admin_token: shared secret for the ``/admin/*`` endpoints.
        container_allowlist: container names the sandbox may query, exact or glob
            patterns (:class:`ContainerAllowlist`).
        query_quota: lifetime queries allowed per enrolled sandbox.
    """
    if not upstream_url:
        raise ValueError("upstream_url is required")
    allowlist = ContainerAllowlist(container_allowlist)
    if not allowlist:
        raise ValueError("container_allowlist must be non-empty")
    if query_quota < 1:
        raise ValueError("query_quota must be >= 1")

    app = web.Application()
    app["upstream_url"] = upstream_url.rstrip("/")
    app["orchestrator_api_key"] = orchestrator_api_key
    app["container_allowlist"] = allowlist.entries
    app["allowlist"] = allowlist
    app["query_quota"] = query_quota
    # Quota counters and scopes are in-memory alongside the shared enrollment
    # registry; all of it resets on proxy restart. The registry runs
    # _forget_sandbox whenever an enrollment ends (revoked, expired, or
    # replaced by a re-enrollment), so a fresh sandbox with a recycled id never
    # inherits a spent budget or an old scope, and neither map outgrows the
    # registry's cap.
    app["query_count_by_id"] = {}
    app["scope_by_id"] = {}
    register_admin_routes(
        app,
        proxy_name="context_proxy",
        admin_token=admin_token,
        on_unenroll=_forget_sandbox,
        enroll_extension=_ContainerScope(),
    )
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.router.add_get("/health", _health_handler)
    # Exactly one proxied route (presign-proxy precedent) — everything else 404s.
    app.router.add_get(_UPSTREAM_PATH, _query_handler)
    return app


def _forget_sandbox(app: web.Application, sandbox_id: str) -> None:
    app["query_count_by_id"].pop(sandbox_id, None)
    app["scope_by_id"].pop(sandbox_id, None)


async def _on_startup(app: web.Application) -> None:
    # Loki's own timeout upstream is 30s; stay above it so the orchestrator's
    # error detail (502 with the Loki message) reaches the agent instead of a
    # generic proxy timeout.
    app["http_client"] = httpx.AsyncClient(timeout=35.0)


async def _on_cleanup(app: web.Application) -> None:
    client = app.get("http_client")
    if client:
        await client.aclose()


async def _health_handler(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "status": "ok",
            "proxy": "context",
            "enrolled": len(request.app["enrollment"]),
            "allowlist_size": len(request.app["container_allowlist"]),
        }
    )


async def _query_handler(request: web.Request) -> web.Response:
    sandbox_id = check_bearer(request, "context_proxy")
    if sandbox_id is None:
        return web.json_response({"error": "unauthorized"}, status=401)

    unknown = set(request.query.keys()) - _ALLOWED_PARAMS
    if unknown:
        # `logql` lands here by design: the raw-LogQL override bypasses the
        # container selector entirely and stays orchestrator-side only.
        return web.json_response(
            {
                "error": f"unsupported parameter(s): {', '.join(sorted(unknown))}",
                "allowed_params": sorted(_ALLOWED_PARAMS),
            },
            status=400,
        )

    container = (request.query.get("container") or "").strip()
    scope: list[str] | None = request.app["scope_by_id"].get(sandbox_id)
    queryable: list[str] = scope if scope is not None else request.app["container_allowlist"]
    # The global allowlist always decides; a scope can only narrow it further.
    canonical = request.app["allowlist"].resolve(container)
    if canonical is not None and scope is not None:
        canonical = {name.lower(): name for name in scope}.get(canonical.lower())
    if not canonical:
        return web.json_response(
            {
                "error": f"container {container!r} is not queryable",
                "allowed_containers": queryable,
            },
            status=400,
        )

    quota = request.app["query_quota"]
    counts = request.app["query_count_by_id"]
    used = counts.get(sandbox_id, 0)
    if used >= quota:
        logger.warning("context_proxy_quota_exhausted", sandbox_id=sandbox_id, quota=quota)
        return web.json_response(
            {"error": f"log query quota exhausted ({quota} per job)"},
            status=429,
        )
    counts[sandbox_id] = used + 1

    params: dict[str, str] = {
        "container": canonical,
        "since_minutes": str(_clamp(request.query.get("since_minutes"), 60, 1, MAX_SINCE_MINUTES)),
        "limit": str(_clamp(request.query.get("limit"), 100, 1, MAX_LIMIT)),
    }
    search = (request.query.get("search") or "")[:MAX_SEARCH_LEN]
    if search:
        params["search"] = search

    # Fresh headers — nothing from the sandbox request is forwarded.
    headers: dict[str, str] = {}
    api_key = request.app["orchestrator_api_key"]
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    client: httpx.AsyncClient = request.app["http_client"]
    upstream = f"{request.app['upstream_url']}{_UPSTREAM_PATH}"
    logger.info(
        "context_proxy_query",
        sandbox_id=sandbox_id,
        container=canonical,
        used=used + 1,
        quota=quota,
    )
    try:
        upstream_resp = await client.get(upstream, params=params, headers=headers)
    except httpx.ConnectError:
        return web.json_response({"error": "Failed to connect to upstream API"}, status=502)
    except httpx.TimeoutException:
        return web.json_response({"error": "Upstream API timeout"}, status=504)

    body = upstream_resp.content
    if upstream_resp.status_code == 200:
        # No sandbox ever receives a raw log line (#294).
        redacted = _redacted_body(body)
        if redacted is None:
            logger.warning("context_proxy_unredactable_body", sandbox_id=sandbox_id)
            return web.json_response({"error": "log backend returned an unreadable result"}, status=502)
        body = redacted

    return web.Response(
        status=upstream_resp.status_code,
        body=body,
        content_type=upstream_resp.headers.get("content-type", "application/json").split(";")[0],
    )
