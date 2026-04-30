"""GitHub API proxy — injects token for REST API calls from sandbox.

Sandbox points its GitHub client at this proxy. The proxy validates the
per-sandbox bearer token before forwarding, then strips it and injects the
real PAT. Bearers are enrolled by the orchestrator at sandbox-create time
via /admin/enroll.
"""

import hashlib
import hmac
import json
import re

import httpx
import structlog
from aiohttp import web

logger = structlog.get_logger(__name__)

GITHUB_API_BASE = "https://api.github.com"

_SANDBOX_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def create_github_proxy_app(github_token: str, admin_token: str) -> web.Application:
    """Create the GitHub API proxy app.

    Args:
        github_token: PAT injected into forwarded requests.
        admin_token: Shared secret with orchestrator for /admin/enroll endpoints.
    """
    if not admin_token:
        raise ValueError("admin_token is required (PROXY_ADMIN_TOKEN must be set)")

    app = web.Application()
    app["github_token"] = github_token
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


def _check_admin(request: web.Request) -> bool:
    presented = request.headers.get("X-Admin-Token", "")
    if not presented:
        logger.warning("github_proxy_unauthorized", path=request.path)
        return False
    result = hmac.compare_digest(_sha256(presented), request.app["admin_token_hash"])
    if not result:
        logger.warning("github_proxy_unauthorized", path=request.path)
    return result


def _check_bearer(request: web.Request) -> "str | None":
    """Return matched sandbox_id if bearer is valid, else None (and log warning)."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        logger.warning("github_proxy_unauthorized", path=request.path)
        return None
    presented = auth[len("Bearer ") :].strip()
    if not presented:
        logger.warning("github_proxy_unauthorized", path=request.path)
        return None
    presented_hash = _sha256(presented)
    raw_match = request.app["enrolled_by_hash"].get(presented_hash)
    if not raw_match:
        logger.warning("github_proxy_unauthorized", path=request.path)
        return None
    matched_sandbox: str = str(raw_match)
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
    logger.info("github_proxy_sandbox_enrolled", sandbox_id=sandbox_id)
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
    logger.info("github_proxy_sandbox_unenrolled", sandbox_id=sandbox_id)
    return web.json_response({"status": "unenrolled"})


async def _proxy_handler(request: web.Request) -> web.StreamResponse:
    """Forward request to GitHub API with injected PAT, after validating bearer."""
    sandbox_id = _check_bearer(request)
    if sandbox_id is None:
        return web.json_response({"error": "unauthorized"}, status=401)

    token = request.app["github_token"]
    path = "/" + request.match_info.get("path", "")
    upstream_url = f"{GITHUB_API_BASE}{path}"
    if request.query_string:
        upstream_url += f"?{request.query_string}"

    headers = dict(request.headers)
    headers.pop("Host", None)
    headers.pop("host", None)
    # Strip sandbox bearer before injecting real PAT
    headers.pop("Authorization", None)
    headers.pop("authorization", None)
    headers["Authorization"] = f"Bearer {token}"
    # Header forwarding is intentionally permissive in Plan A; an allowlist is tracked
    # for follow-up. The sandbox's Authorization header is stripped here; other headers
    # pass through to api.github.com.
    headers["Accept"] = "application/vnd.github+json"
    headers["X-GitHub-Api-Version"] = "2022-11-28"

    body = await request.read()
    client: httpx.AsyncClient = request.app["http_client"]

    logger.info("github_credentials_used", sandbox_id=sandbox_id)

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
