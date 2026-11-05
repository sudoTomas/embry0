"""The git-proxy as the sandbox's remote for the job's repository (#521, ADR 0007).

The sandbox's git reaches the job's repository through the git-proxy, which
adds the forge credential to each upstream request. The credential never
enters the sandbox: not its env, not a credential helper's answer, not a file.

This module checks the parts of that route against strict formats;
:mod:`embry0.execution.sandbox_git` writes it into embry0's own git
configuration and the agent's ``~/.gitconfig``, with the per-sandbox bearer
on stdin. Nothing here runs inside the sandbox.
"""

import re
from dataclasses import dataclass

# Strict format for git proxy URLs: http://host:port (no path, no query).
# ProxyManager now produces Docker DNS names (e.g. http://git-proxy:9101) that
# resolve inside the sandbox-restricted DinD network. We validate defensively so
# that a future change to the source of the URL can't put anything shell- or
# config-active into the commands and files built from it.
_GIT_PROXY_URL_RE = re.compile(r"^http://[a-zA-Z0-9.\-]+:\d+$")

# Strict regex for sandbox bearer tokens. Allows only URL-safe base64 characters
# (produced by secrets.token_urlsafe).
_SANDBOX_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{40,80}$")

# The forge base URL the sandbox clones from (workspace_init.git.clone_origin)
# and the repository path. Same posture: nothing shell-active can pass.
_ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~-]+)*$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,255}(/[A-Za-z0-9_.-]{1,255}){1,19}$")

# The proxy route the git requests go to (embry0.execution.proxy.git_scope.GIT_ROUTE_PREFIX).
_PROXY_GIT_ROUTE = "/git/"


@dataclass(frozen=True)
class ProxyRoute:
    """The git-proxy as the remote for one repository, as checked by :func:`proxy_route`."""

    proxy_base: str  # http://host:port/git/
    origin: str
    repo: str

    @property
    def rewrite_key(self) -> str:
        """``url.<proxy>/git/<repo>.git.insteadOf``: the repository's forge URL goes to the proxy."""
        return f"url.{self.proxy_base}{self.repo}.git.insteadOf"

    @property
    def rewrite_value(self) -> str:
        return f"{self.origin}/{self.repo}.git"

    @property
    def header_key(self) -> str:
        """``http.<proxy>/git/.extraHeader``: the bearer goes to the proxy and nowhere else."""
        return f"http.{self.proxy_base}.extraHeader"


def proxy_route(git_proxy_url: str, *, origin: str, repo: str) -> ProxyRoute:
    """The git-proxy as the remote for ``repo``: every fetch, push and ``ls-remote`` of
    ``<origin>/<repo>.git`` goes to ``<proxy>/git/<repo>.git``, and the bearer to
    ``<proxy>/git/`` and nothing else. Other repositories reach their forge
    directly, without a credential.

    Raises:
        ValueError: if any argument does not match its strict format.
    """
    if not _GIT_PROXY_URL_RE.fullmatch(git_proxy_url):
        raise ValueError(f"git_proxy_url must match http://host:port (got: {git_proxy_url!r})")
    if not _ORIGIN_RE.fullmatch(origin):
        raise ValueError(f"origin must be an http(s) base URL (got: {origin!r})")
    if not _REPO_RE.fullmatch(repo) or any(part in (".", "..") for part in repo.split("/")):
        raise ValueError(f"repo must be a repository path like owner/name (got: {repo!r})")
    return ProxyRoute(proxy_base=f"{git_proxy_url}{_PROXY_GIT_ROUTE}", origin=origin, repo=repo)


def check_sandbox_token(sandbox_token: str) -> None:
    """Raise ValueError unless ``sandbox_token`` looks like a per-sandbox bearer."""
    if not _SANDBOX_TOKEN_RE.fullmatch(sandbox_token):
        raise ValueError("sandbox_token must match ^[A-Za-z0-9_-]{40,80}$")
