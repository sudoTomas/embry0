"""Git operations inside the sandbox container.

Uses the git credential proxy for authentication — tokens never
appear in sandbox process memory or CLI arguments.
"""

import re

import structlog

logger = structlog.get_logger(__name__)

GIT_AUTHOR_NAME = "Athanor"
GIT_AUTHOR_EMAIL = "[removed]"


def build_clone_url(repo: str) -> str:
    """Build HTTPS clone URL for a GitHub repo."""
    return f"https://github.com/{repo}.git"


# Strict format for git proxy URLs: http://host:port (no path, no query).
# ProxyManager now produces Docker DNS names (e.g. http://git-proxy:9101) that
# resolve inside the sandbox-restricted DinD network. We validate defensively so
# that a future change to the source of the URL can't introduce shell injection
# via the `git config` command we build from it.
_GIT_PROXY_URL_RE = re.compile(r"^http://[a-zA-Z0-9.\-]+:\d+$")

# Strict regex for sandbox bearer tokens. Allows only URL-safe base64 characters
# (produced by secrets.token_urlsafe), preventing any shell metacharacters from
# appearing in the single-quoted credential helper string.
_SANDBOX_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{40,80}$")


def build_sandbox_credential_config_cmd(git_proxy_url: str, sandbox_token: str) -> str:
    """Build the bash snippet that configures git inside a sandbox container
    to use the credential proxy with the per-sandbox bearer.

    Used by the orchestrator-side init node to set up git via ``docker exec``.
    Returns a single ``git config --global credential.helper ...`` command
    (no trailing semicolon) suitable for chaining with ``&&``.

    Raises:
        ValueError: if ``git_proxy_url`` does not match ``http://host:port``,
        or if ``sandbox_token`` does not match the strict regex.
    """
    if not _GIT_PROXY_URL_RE.fullmatch(git_proxy_url):
        raise ValueError(f"git_proxy_url must match http://host:port (got: {git_proxy_url!r})")
    if not _SANDBOX_TOKEN_RE.fullmatch(sandbox_token):
        raise ValueError("sandbox_token must match ^[A-Za-z0-9_-]{40,80}$")
    # Single-quoted helper script: bearer is interpolated into a single-quoted
    # shell argument that contains no shell-active characters by virtue of the
    # token regex above.
    return (
        f"git config --global credential.helper "
        f"'!f() {{ curl -sf -H \"Authorization: Bearer {sandbox_token}\" "
        f"{git_proxy_url}/git-credentials; }}; f'"
    )
