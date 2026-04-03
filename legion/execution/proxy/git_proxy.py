"""Git remote proxy — injects GitHub token for git clone/push operations.

Sandbox configures git to use this proxy for credential storage.
The proxy returns credentials in git-credential-fill format.
"""

import structlog
from aiohttp import web

logger = structlog.get_logger(__name__)


def create_git_proxy_app(github_token: str) -> web.Application:
    """Create the git credential proxy app."""
    app = web.Application()
    app["github_token"] = github_token
    app.router.add_get("/health", _health_handler)
    app.router.add_get("/git-credentials", _credentials_handler)
    return app


async def _health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "proxy": "git"})


async def _credentials_handler(request: web.Request) -> web.Response:
    """Return git credentials in git-credential-fill format."""
    token = request.app["github_token"]
    body = (
        "protocol=https\n"
        "host=github.com\n"
        "username=x-access-token\n"
        f"password={token}\n"
    )
    return web.Response(text=body, content_type="text/plain")
