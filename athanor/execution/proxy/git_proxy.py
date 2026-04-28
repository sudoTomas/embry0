"""Git remote proxy — injects GitHub token for git clone/push operations.

Sandbox configures git to use this proxy for credential storage. The proxy
returns credentials in git-credential-fill format ONLY to clients holding a
valid per-sandbox bearer token. Bearers are enrolled by the orchestrator at
sandbox-create time via /admin/enroll.
"""

import hashlib
import hmac

import structlog
from aiohttp import web

logger = structlog.get_logger(__name__)


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
    # sandbox_id -> sha256(sandbox_token)
    app["enrolled"] = {}
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
        return False
    return hmac.compare_digest(_sha256(presented), request.app["admin_token_hash"])


async def _enroll_handler(request: web.Request) -> web.Response:
    if not _check_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "bad_request"}, status=400)
    sandbox_id = body.get("sandbox_id", "")
    sandbox_token = body.get("sandbox_token", "")
    if not sandbox_id or not sandbox_token:
        return web.json_response({"error": "bad_request"}, status=400)
    request.app["enrolled"][sandbox_id] = _sha256(sandbox_token)
    logger.info("git_proxy_sandbox_enrolled", sandbox_id=sandbox_id)
    return web.json_response({"status": "enrolled"})


async def _unenroll_handler(request: web.Request) -> web.Response:
    if not _check_admin(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    sandbox_id = request.match_info["sandbox_id"]
    request.app["enrolled"].pop(sandbox_id, None)
    logger.info("git_proxy_sandbox_unenrolled", sandbox_id=sandbox_id)
    return web.json_response({"status": "unenrolled"})


async def _credentials_handler(request: web.Request) -> web.Response:
    """Return git credentials in git-credential-fill format if bearer is valid."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return web.json_response({"error": "unauthorized"}, status=401)
    presented = auth[len("Bearer "):].strip()
    if not presented:
        return web.json_response({"error": "unauthorized"}, status=401)
    presented_hash = _sha256(presented)

    matched_sandbox: str | None = None
    for sandbox_id, stored_hash in request.app["enrolled"].items():
        if hmac.compare_digest(presented_hash, stored_hash):
            matched_sandbox = sandbox_id
            break

    if not matched_sandbox:
        return web.json_response({"error": "unauthorized"}, status=401)

    logger.info("git_credentials_issued", sandbox_id=matched_sandbox)
    token = request.app["github_token"]
    body = f"protocol=https\nhost=github.com\nusername=x-access-token\npassword={token}\n"
    return web.Response(text=body, content_type="text/plain")
