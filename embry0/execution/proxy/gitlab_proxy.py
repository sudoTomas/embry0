"""GitLab API proxy — injects the token for REST v4 calls from a sandbox (#166).

Same contract as :mod:`embry0.execution.proxy.github_proxy`: the sandbox is
untrusted, so the proxy validates the per-sandbox bearer, drops it, and injects
the real credential upstream. Bearers are enrolled by the orchestrator at
sandbox-create time (shared registry in
:mod:`embry0.execution.proxy.enrollment`). Each enrollment is scoped to the
job's project, and only read calls on an allowlist of that project's resources
are forwarded (#520, policy in :mod:`embry0.execution.proxy.forge_scope`).

Two GitLab differences:

* **Auth header depends on token shape.** A ``glpat-`` personal or group access
  token goes in ``PRIVATE-TOKEN``; only OAuth2 tokens use
  ``Authorization: Bearer``. Sending a PAT as Bearer is rejected outright.
* **No fixed origin.** Self-managed instances mean the base URL is
  configuration, passed in at construction rather than a module constant.
"""

import httpx
import structlog
from aiohttp import web

from embry0.execution.proxy.app_keys import CA_PEM, HTTP_CLIENT
from embry0.execution.proxy.enrollment import check_bearer, payload_token, register_admin_routes
from embry0.execution.proxy.forge_scope import (
    RepoScope,
    gitlab_verdict,
    normalize_gitlab_project,
    policy_denied,
    request_verdict,
)
from embry0.execution.tls import build_ssl_context

logger = structlog.get_logger(__name__)

DEFAULT_GITLAB_API_BASE = "https://gitlab.com/api/v4"

# GitLab's own OAuth2 token prefixes. Anything else is a PAT.
_OAUTH_TOKEN_PREFIXES = ("gloas-", "ya29.")

# Strict forward-header allowlist, same rationale as the GitHub proxy: only
# what GitLab genuinely needs crosses; cookies, spoofed X-Forwarded-*, tracing
# headers and the sandbox bearer are all dropped. PRIVATE-TOKEN /
# Authorization / Accept are proxy-set and never forwarded.
_FORWARDED_REQUEST_HEADERS = (
    "content-type",
    "user-agent",
    "if-none-match",
    "if-modified-since",
)


# This proxy's own state keys; those several proxies share are in ``app_keys``.
GITLAB_TOKEN = web.AppKey("gitlab_token", str)
API_BASE = web.AppKey("api_base", str)


def create_gitlab_proxy_app(
    gitlab_token: str,
    admin_token: str,
    *,
    api_base: str = DEFAULT_GITLAB_API_BASE,
    ca_pem: str = "",
) -> web.Application:
    """Create the GitLab API proxy app.

    Args:
        gitlab_token: token injected into forwarded requests.
        admin_token: shared secret with the orchestrator for /admin/enroll.
        api_base: API origin including ``/api/v4`` — configurable for
            self-managed instances.
        ca_pem: private CA to trust in addition to the system store (spec 1A
            §3.2). The proxy is the second of three clients that break against
            a self-managed instance behind an internal CA.
    """
    app = web.Application()
    app[GITLAB_TOKEN] = gitlab_token
    app[API_BASE] = api_base.rstrip("/")
    app[CA_PEM] = ca_pem
    scope = RepoScope(app, proxy_name="gitlab_proxy", normalize=normalize_gitlab_project)
    register_admin_routes(
        app,
        proxy_name="gitlab_proxy",
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
    ca_pem = app.get(CA_PEM, "")
    if ca_pem:
        app[HTTP_CLIENT] = httpx.AsyncClient(timeout=30.0, verify=build_ssl_context(ca_pem))
        logger.info("gitlab_proxy_private_ca_trusted")
    else:
        app[HTTP_CLIENT] = httpx.AsyncClient(timeout=30.0)


async def _on_cleanup(app: web.Application) -> None:
    client = app.get(HTTP_CLIENT)
    if client:
        await client.aclose()


async def _health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "proxy": "gitlab_api"})


def _auth_headers(token: str) -> dict[str, str]:
    if token.startswith(_OAUTH_TOKEN_PREFIXES):
        return {"Authorization": f"Bearer {token}"}
    return {"PRIVATE-TOKEN": token}


async def _proxy_handler(request: web.Request) -> web.StreamResponse:
    """Forward to the GitLab API with an injected token, after validating the bearer."""
    sandbox_id = check_bearer(request, "gitlab_proxy")
    if sandbox_id is None:
        return web.json_response({"error": "unauthorized"}, status=401)

    verdict = request_verdict(request, sandbox_id, gitlab_verdict)
    if not verdict.allowed:
        return policy_denied(request, "gitlab_proxy", sandbox_id, verdict)

    token = payload_token(request, sandbox_id, request.app[GITLAB_TOKEN])
    # The checked, re-encoded path, never the raw one (see github_proxy).
    upstream_url = f"{request.app[API_BASE]}{verdict.upstream_path}"
    raw_query = request.rel_url.raw_query_string
    if raw_query:
        upstream_url += f"?{raw_query}"

    headers = {k: v for k, v in request.headers.items() if k.lower() in _FORWARDED_REQUEST_HEADERS}
    headers.update(_auth_headers(token))
    headers["Accept"] = "application/json"

    body = await request.read()
    client: httpx.AsyncClient = request.app[HTTP_CLIENT]

    logger.info("gitlab_credentials_used", sandbox_id=sandbox_id, method=request.method, path=verdict.upstream_path)

    try:
        resp = await client.request(
            method=request.method,
            url=upstream_url,
            headers=headers,
            content=body,
        )
        raw_ct = resp.headers.get("content-type", "application/json")
        content_type = raw_ct.split(";")[0].strip()
        return web.Response(
            status=resp.status_code,
            body=resp.content,
            content_type=content_type,
        )
    except httpx.ConnectError:
        return web.json_response({"error": "Failed to connect to GitLab API"}, status=502)
    except httpx.TimeoutException:
        return web.json_response({"error": "GitLab API timeout"}, status=504)
