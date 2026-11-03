"""GitHub API proxy — injects token for REST API calls from sandbox.

Sandbox points its GitHub client at this proxy. The proxy validates the
per-sandbox bearer token before forwarding, then strips it and injects the
real PAT. Bearers are enrolled by the orchestrator at sandbox-create time
via /admin/enroll (shared hardened registry — TTL, cap, admin backoff — in
:mod:`embry0.execution.proxy.enrollment`, RAV-1070).

Each enrollment is scoped to the job's repository, and only read calls on an
allowlist of that repository's resources are forwarded (#520, policy in
:mod:`embry0.execution.proxy.forge_scope`). Everything else is a 403.
"""

import httpx
import structlog
from aiohttp import web

from embry0.execution.proxy.app_keys import GITHUB_TOKEN, HTTP_CLIENT
from embry0.execution.proxy.enrollment import check_bearer, payload_token, register_admin_routes
from embry0.execution.proxy.forge_scope import (
    RepoScope,
    github_verdict,
    normalize_github_repo,
    policy_denied,
    request_verdict,
)

logger = structlog.get_logger(__name__)

GITHUB_API_BASE = "https://api.github.com"

# RAV-1070: strict forward-header allowlist. The sandbox is untrusted — only
# headers GitHub genuinely needs cross the proxy; everything else (cookies,
# spoofed X-Forwarded-*, tracing headers, the sandbox bearer) is dropped.
# Authorization / Accept / X-GitHub-Api-Version are proxy-set, never forwarded.
_FORWARDED_REQUEST_HEADERS = (
    "content-type",
    "user-agent",
    "if-none-match",
    "if-modified-since",
)


def create_github_proxy_app(github_token: str, admin_token: str) -> web.Application:
    """Create the GitHub API proxy app.

    Args:
        github_token: PAT injected into forwarded requests.
        admin_token: Shared secret with orchestrator for /admin/enroll endpoints.
    """
    app = web.Application()
    app[GITHUB_TOKEN] = github_token
    scope = RepoScope(app, proxy_name="github_proxy", normalize=normalize_github_repo)
    register_admin_routes(
        app,
        proxy_name="github_proxy",
        admin_token=admin_token,
        accepts_payload_token=True,
        on_unenroll=scope.forget,
        enroll_extension=scope,
    )
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.router.add_get("/health", _health_handler)
    app.router.add_route("*", "/{path:.*}", _proxy_handler)
    return app


async def _on_startup(app: web.Application) -> None:
    app[HTTP_CLIENT] = httpx.AsyncClient(timeout=30.0)


async def _on_cleanup(app: web.Application) -> None:
    client = app.get(HTTP_CLIENT)
    if client:
        await client.aclose()


async def _health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "proxy": "github_api"})


async def _proxy_handler(request: web.Request) -> web.StreamResponse:
    """Forward request to GitHub API with injected PAT, after validating bearer."""
    sandbox_id = check_bearer(request, "github_proxy")
    if sandbox_id is None:
        return web.json_response({"error": "unauthorized"}, status=401)

    verdict = request_verdict(request, sandbox_id, github_verdict)
    if not verdict.allowed:
        return policy_denied(request, "github_proxy", sandbox_id, verdict)

    token = payload_token(request, sandbox_id, request.app[GITHUB_TOKEN])
    # The checked, re-encoded path, never the raw one: httpx would collapse
    # dot segments in a raw path after the check.
    upstream_url = f"{GITHUB_API_BASE}{verdict.upstream_path}"
    raw_query = request.rel_url.raw_query_string
    if raw_query:
        upstream_url += f"?{raw_query}"

    headers = {k: v for k, v in request.headers.items() if k.lower() in _FORWARDED_REQUEST_HEADERS}
    headers["Authorization"] = f"Bearer {token}"
    headers["Accept"] = "application/vnd.github+json"
    headers["X-GitHub-Api-Version"] = "2022-11-28"

    body = await request.read()
    client: httpx.AsyncClient = request.app[HTTP_CLIENT]

    logger.info("github_credentials_used", sandbox_id=sandbox_id, method=request.method, path=verdict.upstream_path)

    try:
        resp = await client.request(
            method=request.method,
            url=upstream_url,
            headers=headers,
            content=body,
        )
        # Strip charset from content-type — aiohttp requires them separate
        raw_ct = resp.headers.get("content-type", "application/json")
        content_type = raw_ct.split(";")[0].strip()
        return web.Response(
            status=resp.status_code,
            body=resp.content,
            content_type=content_type,
        )
    except httpx.ConnectError:
        return web.json_response({"error": "Failed to connect to GitHub API"}, status=502)
    except httpx.TimeoutException:
        return web.json_response({"error": "GitHub API timeout"}, status=504)
