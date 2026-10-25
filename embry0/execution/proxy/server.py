"""Container entrypoint for the embry0-proxy image.

Dispatches to one of the stateless proxy apps based on PROXY_TYPE env:
- git     -> git_proxy.create_git_proxy_app(GITHUB_TOKEN, PROXY_ADMIN_TOKEN, ca_pem=GITLAB_CA_PEM)
- github  -> github_proxy.create_github_proxy_app(GITHUB_TOKEN, PROXY_ADMIN_TOKEN)
- auth    -> auth_proxy.create_auth_proxy_app(ANTHROPIC_API_KEY, PROXY_ADMIN_TOKEN)
- gitlab  -> gitlab_proxy.create_gitlab_proxy_app(GITLAB_TOKEN, PROXY_ADMIN_TOKEN,
             api_base=GITLAB_API_BASE, ca_pem=GITLAB_CA_PEM)  # spec 1A 3.2
- xai     -> xai_proxy.create_xai_proxy_app(PROXY_ADMIN_TOKEN)  # access token pushed at runtime
- minio   -> minio_proxy.create_minio_proxy_app(UPSTREAM_URL)
- presign -> presign_proxy.create_presign_proxy_app(UPSTREAM_URL)
- local_llm -> local_llm_proxy.create_local_llm_proxy_app(UPSTREAM_URL, PROXY_ADMIN_TOKEN,
             upstream_api_key=UPSTREAM_API_KEY, max_request_bytes=MAX_REQUEST_BYTES)
- context -> context_proxy.create_context_proxy_app(UPSTREAM_URL, ORCHESTRATOR_API_KEY,
             PROXY_ADMIN_TOKEN, CONTAINER_ALLOWLIST, QUERY_QUOTA)  # RAV-1140
             (LOG_REDACTION_FIELDS: payload fields its redaction also removes, #294)

Listens on 0.0.0.0:LISTEN_PORT (default depends on proxy type).
PROXY_ADMIN_TOKEN must be set for credential-injection proxies (git/github/auth)
and for the context and local_llm proxies (per-sandbox enrollment); the network-plumbing proxies
(minio/presign) do not require it.
"""

from __future__ import annotations

import os
import ssl

import structlog
from aiohttp import web

from embry0.execution.proxy.auth_proxy import create_auth_proxy_app
from embry0.execution.proxy.context_proxy import create_context_proxy_app
from embry0.execution.proxy.git_proxy import create_git_proxy_app
from embry0.execution.proxy.github_proxy import create_github_proxy_app
from embry0.execution.proxy.local_llm_proxy import DEFAULT_MAX_REQUEST_BYTES, create_local_llm_proxy_app
from embry0.execution.proxy.minio_proxy import create_minio_proxy_app
from embry0.execution.proxy.presign_proxy import create_presign_proxy_app
from embry0.execution.proxy.xai_proxy import create_xai_proxy_app
from embry0.execution.tls import build_ssl_context
from embry0.safety.redaction import register_redacted_fields

logger = structlog.get_logger(__name__)

DEFAULT_PORTS = {
    "git": 9101,
    "github": 9103,
    "auth": 9100,
    "xai": 9106,
    "minio": 9100,
    "presign": 9104,
    "context": 9107,
    "local_llm": 9108,
    # 9109+ is free; 9100/9101/9103-9108 are taken.
    "gitlab": 9105,
}


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        msg = f"{name} is required for this proxy type"
        raise ValueError(msg)
    return value


def _gitlab_ca_pem() -> str:
    """Private CA for the GitLab API proxy (spec 1A §3.2), or "" when unset.

    Accepts the PEM either raw or base64-encoded. The encoding is not a
    preference: ProxyManager delivers proxy env through ``docker --env-file``,
    which takes each value literally *up to the newline* — a multi-line PEM
    would arrive truncated to its first line. A garbled value degrades to the
    system trust store with a warning rather than refusing to start the proxy;
    the resulting handshake failure is the louder, more specific signal.

    Whichever branch produced it, the candidate is parsed here rather than
    trusted on the strength of its header: that truncated first line *is*
    ``-----BEGIN CERTIFICATE-----``, so a marker check alone would hand the
    proxy a PEM that only explodes later, in ``_on_startup``, as an unhandled
    SSLError — precisely the crash this fallback exists to prevent.
    """
    raw = os.environ.get("GITLAB_CA_PEM", "")
    if not raw:
        return ""
    if "BEGIN CERTIFICATE" in raw:
        candidate = raw
    else:
        import base64
        import binascii

        try:
            # Whitespace-stripped first: base64(1) and openssl base64 wrap at
            # 76 columns, and validate=True rejects the newlines outright.
            candidate = base64.b64decode("".join(raw.split()), validate=True).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            logger.warning("gitlab_ca_pem_unreadable", length=len(raw))
            return ""
    try:
        build_ssl_context(candidate)
    except (ssl.SSLError, ValueError):
        logger.warning("gitlab_ca_pem_unreadable", length=len(raw))
        return ""
    return candidate


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
        # GITHUB_TOKEN is optional here: it is only the fallback credential
        # for payload-less (pre-#166, GitHub-only) enrollments. A GitLab-only
        # deployment runs the git proxy with no GitHub token at all — every
        # credential it uses comes from the per-sandbox enrollment payload.
        # It forwards to the forge itself (#521), so it trusts the GitLab
        # private CA like the gitlab-proxy does.
        return create_git_proxy_app(os.environ.get("GITHUB_TOKEN", ""), admin_token, ca_pem=_gitlab_ca_pem())
    if proxy_type == "github":
        # Optional for the same reason (#424): a registry-only deployment has
        # no baked token and every request uses the enrolled payload token.
        return create_github_proxy_app(os.environ.get("GITHUB_TOKEN", ""), admin_token)
    if proxy_type == "gitlab":
        from embry0.execution.proxy.gitlab_proxy import DEFAULT_GITLAB_API_BASE, create_gitlab_proxy_app

        return create_gitlab_proxy_app(
            os.environ.get("GITLAB_TOKEN", ""),  # optional, see the github branch
            admin_token,
            api_base=os.environ.get("GITLAB_API_BASE") or DEFAULT_GITLAB_API_BASE,
            ca_pem=_gitlab_ca_pem(),
        )
    if proxy_type == "auth":
        return create_auth_proxy_app(_require_env("ANTHROPIC_API_KEY"), admin_token)
    if proxy_type == "xai":
        # No static credential: the SuperGrok access token is pushed at runtime via
        # /admin/token by the orchestrator's refresher.
        return create_xai_proxy_app(admin_token)
    if proxy_type == "local_llm":
        # The operator's own model server: one upstream, its key held here.
        return create_local_llm_proxy_app(
            _require_env("UPSTREAM_URL"),
            admin_token,
            upstream_api_key=os.environ.get("UPSTREAM_API_KEY", ""),
            max_request_bytes=int(os.environ.get("MAX_REQUEST_BYTES") or DEFAULT_MAX_REQUEST_BYTES),
        )
    if proxy_type == "context":
        # RAV-1140: mediated read-only log queries for watcher analysis jobs.
        # ORCHESTRATOR_API_KEY may be empty (dev mode — upstream auth is skipped
        # there too), but warn so a misconfigured prod deploy is visible.
        api_key = os.environ.get("ORCHESTRATOR_API_KEY", "")
        if not api_key:
            logger.warning("context_proxy_no_api_key")
        allowlist = [c for c in _require_env("CONTAINER_ALLOWLIST").split(",") if c.strip()]
        quota = int(os.environ.get("QUERY_QUOTA", "30"))
        register_redacted_fields(os.environ.get("LOG_REDACTION_FIELDS", ""))
        return create_context_proxy_app(
            _require_env("UPSTREAM_URL"),
            api_key,
            admin_token,
            allowlist,
            quota,
        )

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
