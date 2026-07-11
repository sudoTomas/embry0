"""Container entrypoint for the embry0-proxy image.

Dispatches to one of the stateless proxy apps based on PROXY_TYPE env:
- git     -> git_proxy.create_git_proxy_app(GITHUB_TOKEN, PROXY_ADMIN_TOKEN)
- github  -> github_proxy.create_github_proxy_app(GITHUB_TOKEN, PROXY_ADMIN_TOKEN)
- auth    -> auth_proxy.create_auth_proxy_app(ANTHROPIC_API_KEY, PROXY_ADMIN_TOKEN)
- minio   -> minio_proxy.create_minio_proxy_app(UPSTREAM_URL)
- presign -> presign_proxy.create_presign_proxy_app(UPSTREAM_URL)

Listens on 0.0.0.0:LISTEN_PORT (default depends on proxy type).
PROXY_ADMIN_TOKEN must be set for credential-injection proxies (git/github/auth);
the network-plumbing proxies (minio/presign) do not require it.
"""

from __future__ import annotations

import os

import structlog
from aiohttp import web

from embry0.execution.proxy.auth_proxy import create_auth_proxy_app
from embry0.execution.proxy.git_proxy import create_git_proxy_app
from embry0.execution.proxy.github_proxy import create_github_proxy_app
from embry0.execution.proxy.minio_proxy import create_minio_proxy_app
from embry0.execution.proxy.presign_proxy import create_presign_proxy_app

logger = structlog.get_logger(__name__)

DEFAULT_PORTS = {
    "git": 9101,
    "github": 9103,
    "auth": 9100,
    "minio": 9100,
    "presign": 9104,
}


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        msg = f"{name} is required for this proxy type"
        raise ValueError(msg)
    return value


def build_app_from_env() -> web.Application:
    """Construct the aiohttp app for the configured PROXY_TYPE."""
    proxy_type = os.environ.get("PROXY_TYPE", "")
    if not proxy_type:
        msg = "PROXY_TYPE env var is required"
        raise ValueError(msg)

    if proxy_type == "minio":
        # No admin token: the proxy is pure network plumbing. Auth happens
        # at MinIO via presigned-URL signatures.
        return create_minio_proxy_app(_require_env("UPSTREAM_URL"))
    if proxy_type == "presign":
        # Same: orchestrator validates the sandbox bearer token in the body.
        return create_presign_proxy_app(_require_env("UPSTREAM_URL"))

    admin_token = _require_env("PROXY_ADMIN_TOKEN")

    if proxy_type == "git":
        return create_git_proxy_app(_require_env("GITHUB_TOKEN"), admin_token)
    if proxy_type == "github":
        return create_github_proxy_app(_require_env("GITHUB_TOKEN"), admin_token)
    if proxy_type == "auth":
        return create_auth_proxy_app(_require_env("ANTHROPIC_API_KEY"), admin_token)

    msg = f"Unknown PROXY_TYPE: {proxy_type!r}"
    raise ValueError(msg)


def listen_port_from_env() -> int:
    """Resolve the listen port. LISTEN_PORT overrides the per-type default."""
    explicit = os.environ.get("LISTEN_PORT")
    if explicit:
        return int(explicit)
    proxy_type = os.environ.get("PROXY_TYPE", "")
    return DEFAULT_PORTS.get(proxy_type, 9100)


def main() -> None:
    app = build_app_from_env()
    port = listen_port_from_env()
    logger.info(
        "embry0_proxy_starting",
        proxy_type=os.environ.get("PROXY_TYPE"),
        port=port,
    )
    web.run_app(app, host="0.0.0.0", port=port, print=None)  # noqa: S104


if __name__ == "__main__":
    main()
