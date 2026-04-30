"""Git remote proxy — injects GitHub token for git clone/push operations.

Sandbox configures git to use this proxy for credential storage. The proxy
returns credentials in git-credential-fill format ONLY to clients holding a
valid per-sandbox bearer token. Bearers are enrolled by the orchestrator at
sandbox-create time via /admin/enroll.
"""

import hashlib
import hmac
import json
import re

import structlog
from aiohttp import web

logger = structlog.get_logger(__name__)

_SANDBOX_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def create_git_proxy_app(github_token: str, admin_token: str) -> web.Application:
    """Create the git credential proxy app.

    Args:
        github_token: PAT to issue when a valid bearer is presented.
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
    # sha256(sandbox_token) -> sandbox_id; used by credentials handler for O(1) lookup
    app["enrolled_by_hash"] = {}
    app.router.add_get("/health", _health_handler)
    app.router.add_get("/git-credentials", _credentials_handler)
    app.router.add_post("/admin/enroll", _enroll_handler)
    app.router.add_delete("/admin/enroll/{sandbox_id}", _unenroll_handler)
    return app


async def _health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "proxy": "git"})


def _check_admin(request: web.Request) -> bool:
    presented = request.headers.get("X-Admin-Token", "")
    if not presented:
        logger.warning("git_proxy_unauthorized", path=request.path)
        return False
    result = hmac.compare_digest(_sha256(presented), request.app["admin_token_hash"])
    if not result:
        logger.warning("git_proxy_unauthorized", path=request.path)
    return result


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
    logger.info("git_proxy_sandbox_enrolled", sandbox_id=sandbox_id)
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
    logger.info("git_proxy_sandbox_unenrolled", sandbox_id=sandbox_id)
    return web.json_response({"status": "unenrolled"})


async def _credentials_handler(request: web.Request) -> web.Response:
    """Return git credentials in git-credential-fill format if bearer is valid."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        logger.warning("git_proxy_unauthorized", path=request.path)
        return web.json_response({"error": "unauthorized"}, status=401)
    presented = auth[len("Bearer ") :].strip()
    if not presented:
        logger.warning("git_proxy_unauthorized", path=request.path)
        return web.json_response({"error": "unauthorized"}, status=401)
    presented_hash = _sha256(presented)

    matched_sandbox = request.app["enrolled_by_hash"].get(presented_hash)
    if not matched_sandbox:
        logger.warning("git_proxy_unauthorized", path=request.path)
        return web.json_response({"error": "unauthorized"}, status=401)

    logger.info("git_credentials_issued", sandbox_id=matched_sandbox)
    token = request.app["github_token"]
    body = f"protocol=https\nhost=github.com\nusername=x-access-token\npassword={token}\n"
    return web.Response(text=body, content_type="text/plain")
