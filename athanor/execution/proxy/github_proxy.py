"""GitHub API proxy — injects token for REST API calls from sandbox.

Sandbox points its GitHub client at this proxy. The proxy adds
Authorization header and forwards to api.github.com.
"""

import httpx
import structlog
from aiohttp import web

logger = structlog.get_logger(__name__)

GITHUB_API_BASE = "https://api.github.com"


def create_github_proxy_app(github_token: str, admin_token: str = "") -> web.Application:
    """Create the GitHub API proxy app.

    Args:
        github_token: PAT injected into forwarded requests.
        admin_token: Reserved for Task 7 enrollment endpoints; accepted but not
            yet enforced. Passing a non-empty value is a no-op until Task 7 lands.
    """
    app = web.Application()
    app["github_token"] = github_token
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.router.add_get("/health", _health_handler)
    app.router.add_route("*", "/{path:.*}", _proxy_handler)
    return app


async def _on_startup(app: web.Application) -> None:
    app["http_client"] = httpx.AsyncClient(timeout=30.0)


async def _on_cleanup(app: web.Application) -> None:
    client = app.get("http_client")
    if client:
        await client.aclose()


async def _health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "proxy": "github_api"})


async def _proxy_handler(request: web.Request) -> web.StreamResponse:
    """Forward request to GitHub API with injected token."""
    token = request.app["github_token"]
    path = "/" + request.match_info.get("path", "")
    upstream_url = f"{GITHUB_API_BASE}{path}"
    if request.query_string:
        upstream_url += f"?{request.query_string}"

    headers = dict(request.headers)
    headers.pop("Host", None)
    headers.pop("host", None)
    headers["Authorization"] = f"Bearer {token}"
    headers["Accept"] = "application/vnd.github+json"
    headers["X-GitHub-Api-Version"] = "2022-11-28"

    body = await request.read()
    client: httpx.AsyncClient = request.app["http_client"]

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
