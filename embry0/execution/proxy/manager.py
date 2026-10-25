"""Proxy lifecycle manager — launches credential-injecting proxies as DinD containers.

Five stateless proxies run as containers on the sandbox-restricted network
so sandboxes can resolve them by Docker DNS:

Credential-injection proxies (hold orchestrator credentials, attach also to
sandbox-internet to reach external APIs):
- git-proxy     → forwards git smart HTTP for the job's repository with the
                  forge token added upstream (#521, ADR 0007)
- github-proxy  → injects GITHUB_TOKEN into REST/GraphQL calls
- auth-proxy    → injects ANTHROPIC_API_KEY (currently dead path)

Network-plumbing proxies (Phase 1.5 — sandboxes inside DinD cannot reach
host backend services because DinD has its own daemon and DNS namespace;
these proxies live on sandbox-internet AND sandbox-restricted, holding the
host-side IPs of minio and orchestrator):
- minio-proxy   → forwards to host minio:9000 (S3 API for QA artifacts)
- presign-proxy → forwards to host orchestrator:8000 (POST /internal/qa/presign)
- context-proxy → forwards to host orchestrator:8000 (GET /api/v1/logs/query),
  RAV-1140: mediated read-only log drill-down for watcher analysis jobs. Holds
  the orchestrator API key and enforces container allowlist + query quota.
  Enrollment is per-job opt-in via enroll_context(), NOT the broadcast.

The per-job embry0 API proxy (CreateIssue / RequestInput / UpdateStatus)
is deferred — it has DB coupling that needs separate rework. Workflow
registration validation rejects templates that declare those tools (see
embry0/workflows/_validation.py).
"""

from __future__ import annotations

import asyncio
import ipaddress
import secrets
import socket
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import aiohttp
import structlog

from embry0.execution.docker_client import DockerClient
from embry0.execution.image_registry import qualify_image
from embry0.execution.proxy.forge_scope import SCOPE_FIELD, normalize_github_repo, normalize_gitlab_project
from embry0.execution.proxy.git_scope import (
    JOB_FIELD,
    PUSH_BRANCHES_FIELD,
    PUSH_FIELD,
    UPSTREAM_FIELD,
    normalize_git_repo,
    normalize_push_branch,
    normalize_upstream,
)

logger = structlog.get_logger(__name__)

_BASE_IMAGE = "embry0-proxy:latest"
_RESTRICTED = "sandbox-restricted"
_INTERNET = "sandbox-internet"

# Fixed shared proxy names. These are long-lived DinD containers, not per-job
# proxies. A cleanup sweep may remove only names from this tuple that this
# process failed to remove and that are not in the current launched set —
# never an arbitrary ``*-proxy`` container, which would cut git/GitHub/MinIO
# /xAI/context for every sandbox still running.
_PROXY_CONTAINER_NAMES: tuple[str, ...] = (
    "git-proxy",
    "github-proxy",
    "gitlab-proxy",
    "auth-proxy",
    "xai-proxy",
    "minio-proxy",
    "presign-proxy",
    "context-proxy",
    "local-llm-proxy",
)

# Connection failures only. A name conflict or a permission error will not
# recover by waiting, and a blanket retry inside DockerClient.run_cmd would
# stall every sandbox launch on a daemon blip.
_CLEANUP_ATTEMPTS = 4
_CLEANUP_BACKOFF_S: tuple[float, ...] = (1.0, 2.0, 4.0)
# ``docker rm -f`` of a missing name exits 0 immediately. This ceiling only
# matters when the CLI hangs instead of failing the connect; keep it well
# under uvicorn's graceful-shutdown budget.
_CLEANUP_RM_TIMEOUT_S = 8
# Follow-up removals while this process is still up. Stop cancels the task;
# the next start() pre-clean is the recovery path after a shutdown miss.
_SWEEP_DELAYS_S: tuple[float, ...] = (15.0, 30.0, 60.0)

# #520: the forge API proxies forward only read calls on the repositories an
# enrollment names. Proxy name -> (its forge, how that forge spells a repo).
_FORGE_API_PROXIES: dict[str, tuple[str, Callable[[str], str | None]]] = {
    "github-proxy": ("github", normalize_github_repo),
    "gitlab-proxy": ("gitlab", normalize_gitlab_project),
}

_DAEMON_UNREACHABLE_MARKERS: tuple[str, ...] = (
    "cannot connect to the docker daemon",
    "is the docker daemon running",
    "error during connect",
    "connection refused",
    "connection reset by peer",
    "dial tcp",
)


# What ``docker exec`` says when the proxy container exited or was removed
# (#626). Distinct from the daemon being unreachable: the daemon answered.
_PROXY_GONE_MARKERS: tuple[str, ...] = (
    "is not running",
    "no such container",
)


def _proxy_gone(exc: BaseException) -> bool:
    """True when ``exc`` is ``docker exec`` refused because the proxy container is stopped or missing."""
    text = str(exc).lower()
    return any(marker in text for marker in _PROXY_GONE_MARKERS)


def _daemon_unreachable(exc: BaseException) -> bool:
    """True when ``exc`` is the Docker CLI failing to reach the daemon.

    Matched on the error text rather than the exception type: ``run_cmd``
    wraps a non-zero exit in RuntimeError, and a timeout is a different
    failure (the CLI hung) that cleanup does not retry.
    """
    text = str(exc).lower()
    return any(marker in text for marker in _DAEMON_UNREACHABLE_MARKERS)


def resolve_upstream_url(url: str) -> str:
    """``url`` with its host replaced by the address it resolves to, for a proxy in DinD.

    DinD resolves no name the orchestrator's networks know, so an http
    upstream named by such a name is baked in by address, as the backend
    proxies' are. An https upstream keeps its name for the certificate check,
    and a name that does not resolve here is kept for DinD to try.
    """
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme != "http" or not host:
        return url
    try:
        ipaddress.ip_address(host)
        return url
    except ValueError:
        pass
    try:
        address = socket.gethostbyname(host)
    except OSError:
        logger.warning("local_llm_upstream_unresolved", host=host)
        return url
    netloc = address if parts.port is None else f"{address}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _resolve_backend_url(host: str, port: int) -> str:
    """Return ``http://<ip>:<port>`` where ``<ip>`` is ``host`` resolved via DNS.

    The minio-proxy / presign-proxy run inside DinD and can reach host backend
    services via NAT through the sandbox-internet bridge — but ONLY by IP, not
    by name (DinD's DNS doesn't know about the host docker daemon's services).
    The orchestrator IS on backend, so it can resolve the names; we bake the
    IP into the proxy's UPSTREAM_URL at proxy launch time.

    Raises RuntimeError on resolution failure so startup fails loudly rather
    than launching a proxy that will 502 every request.
    """
    try:
        ip = socket.gethostbyname(host)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot resolve `{host}` from the orchestrator. The orchestrator "
            f"must be on a docker network that has access to `{host}` for the "
            "Phase 1.5 proxies to function."
        ) from exc
    return f"http://{ip}:{port}"


class ProxyManager:
    """Manages proxy container lifecycle for sandbox agent access."""

    def __init__(
        self,
        docker: DockerClient,
        *,
        proxy_admin_token: str,
        image_registry: str = "",
        enroll_ttl_seconds: int = 86_400,
    ) -> None:
        if not proxy_admin_token:
            raise ValueError("proxy_admin_token is required")
        self._docker = docker
        self._launched: list[str] = []
        # Names this process tried to remove and could not, because the daemon
        # was unreachable. The delayed sweep may touch only these, and only
        # while they are absent from ``_launched``.
        self._pending_removal: set[str] = set()
        self._sweep_task: asyncio.Task[None] | None = None
        self._stopping = False
        # The launched proxies that are also on sandbox-internet. A sandbox on
        # that network resolves these names to their address THERE, so its
        # egress rules must except them (#472).
        self._internet_attached: list[str] = []
        self._proxy_admin_token = proxy_admin_token
        # RAV-1070: every enrollment carries a TTL so bearers left behind by a
        # missed unenroll expire on their own. Comfortably above the longest
        # legitimate job; unenroll on job completion remains the primary path.
        self._enroll_ttl_seconds = enroll_ttl_seconds
        self._image = qualify_image(_BASE_IMAGE, image_registry)
        self._http: aiohttp.ClientSession | None = None
        # #626: what a proxy found stopped is recreated from. The env is what
        # start() launched it with; it holds credentials, as this process does.
        self._launch_specs: dict[str, tuple[dict[str, str], bool]] = {}
        # #626: the admin state each proxy holds in memory only, replayed into
        # a recreated proxy: proxy -> key -> (path, port, body, monotonic expiry).
        self._admin_state: dict[str, dict[str, tuple[str, int, dict[str, Any], float]]] = {}
        self._revive_locks: dict[str, asyncio.Lock] = {}
        self._on_recreated: Callable[[], Awaitable[None]] | None = None
        self.auth_proxy_url: str = ""
        self.xai_proxy_url: str = ""
        self.git_proxy_url: str = ""
        self.github_proxy_url: str = ""
        self.gitlab_proxy_url: str = ""
        self.minio_proxy_url: str = ""
        self.presign_proxy_url: str = ""
        self.context_proxy_url: str = ""
        self.local_llm_proxy_url: str = ""

    async def start(
        self,
        anthropic_api_key: str = "",
        github_token: str = "",
        gitlab_token: str = "",
        gitlab_api_base: str = "",
        gitlab_ca_pem: str = "",
        enable_auth_proxy: bool = False,
        enable_xai_proxy: bool = False,
        enable_context_proxy: bool = False,
        orchestrator_api_key: str = "",
        context_container_allowlist: str = "",
        context_query_quota: int = 30,
        log_redaction_fields: str = "",
        has_github_credential: bool | None = None,
        has_gitlab_credential: bool | None = None,
        local_llm_upstream: str = "",
        local_llm_api_key: str = "",
        local_llm_max_request_bytes: int = 0,
    ) -> None:
        """Start the credential proxies as DinD containers.

        The auth-proxy is only launched when ``enable_auth_proxy`` is true AND
        a key is provided. Defaults False because no consumer is wired (the
        Claude Agent SDK currently reads the OAuth token directly via env, not
        through a proxy).

        Idempotent: any pre-existing containers with the same names are removed first.

        ``has_*_credential`` come from the credential registry; None falls back to the token arguments.

        ``local_llm_upstream`` launches the local-llm-proxy, which forwards a
        sandbox's model calls to that one API root (the operator's model
        server); ``local_llm_api_key`` is the key that server checks, if any.
        """
        if self._http is None:
            self._http = aiohttp.ClientSession()
        # A manager reused after stop() (tests, a future restart-in-place) is
        # no longer shutting down; a sweep from the previous life must not run.
        self._stopping = False
        await self._cancel_sweep()

        # ``docker rm -f`` of a missing name exits 0, so a swallowed error here
        # is never "it didn't exist". A daemon blip is retried; anything else
        # is logged and left for the sweep only if it was a connect failure.
        failed_preclean = await self._remove_proxies(list(_PROXY_CONTAINER_NAMES), phase="start_preclean")
        self._pending_removal = set(failed_preclean)
        # Fresh proxies hold no enrollment; a paused sandbox is re-enrolled on resume.
        self._admin_state.clear()

        github_present = bool(github_token) if has_github_credential is None else has_github_credential
        gitlab_present = bool(gitlab_token) if has_gitlab_credential is None else has_gitlab_credential

        # The git proxy serves clone/fetch/push for BOTH forges: the actual
        # credential comes from each sandbox's enrollment payload
        # (forge-resolved token + host), and the baked GITHUB_TOKEN is only
        # the fallback for payload-less (pre-#166, GitHub-only) enrollments.
        # Gate on either token — a GitLab-only deployment (no GITHUB_TOKEN)
        # still needs the proxy container, or every clone dies with
        # "could not read Username for 'https://gitlab.com'".
        # attach_internet=True since #521: it forwards git's smart HTTP to the
        # forge itself instead of handing the token to the sandbox, and it
        # needs the GitLab private CA for the same reason the gitlab-proxy does.
        if github_present or gitlab_present:
            git_env = {
                "PROXY_TYPE": "git",
                "GITHUB_TOKEN": github_token,
                "LISTEN_PORT": "9101",
                "PROXY_ADMIN_TOKEN": self._proxy_admin_token,
            }
            if gitlab_ca_pem:
                import base64

                # Base64 for the same env-file reason as the gitlab-proxy below.
                git_env["GITLAB_CA_PEM"] = base64.b64encode(gitlab_ca_pem.encode()).decode()
            await self._launch(name="git-proxy", env=git_env, attach_internet=True)
            self.git_proxy_url = "http://git-proxy:9101"
            logger.info("git_proxy_started")

        if github_present:
            await self._launch(
                name="github-proxy",
                env={
                    "PROXY_TYPE": "github",
                    "GITHUB_TOKEN": github_token,
                    "LISTEN_PORT": "9103",
                    "PROXY_ADMIN_TOKEN": self._proxy_admin_token,
                },
                attach_internet=True,
            )
            self.github_proxy_url = "http://github-proxy:9103"
            logger.info("github_proxy_started")

        # GitLab API proxy (#166). Independent of github_token: a deployment
        # may target GitLab only. attach_internet=True: it forwards calls.
        if gitlab_present:
            gitlab_env = {
                "PROXY_TYPE": "gitlab",
                "GITLAB_TOKEN": gitlab_token,
                "GITLAB_API_BASE": gitlab_api_base,
                "LISTEN_PORT": "9105",
                "PROXY_ADMIN_TOKEN": self._proxy_admin_token,
            }
            if gitlab_ca_pem:
                # Spec 1A §3.2. Base64 is load-bearing, not tidiness: proxy env
                # is delivered through `docker --env-file`, which reads each
                # value literally up to the newline — a raw multi-line PEM
                # would arrive as its BEGIN line and nothing else. server.py
                # accepts either encoding.
                import base64

                gitlab_env["GITLAB_CA_PEM"] = base64.b64encode(gitlab_ca_pem.encode()).decode()
            await self._launch(name="gitlab-proxy", env=gitlab_env, attach_internet=True)
            self.gitlab_proxy_url = "http://gitlab-proxy:9105"
            logger.info(
                "gitlab_proxy_started",
                api_base=gitlab_api_base,
                private_ca=bool(gitlab_ca_pem),
            )

        # Phase 1.5 — network-plumbing proxies. No credentials, no admin
        # token enrollment (auth happens at the upstream MinIO via presigned
        # signature, and at the upstream orchestrator via sandbox bearer token
        # in the request body). UPSTREAM_URL is resolved to an IP because the
        # proxies live in DinD and must reach the host backend network by IP
        # (DinD's DNS doesn't know about host services).
        await self._launch(
            name="minio-proxy",
            env={
                "PROXY_TYPE": "minio",
                "UPSTREAM_URL": _resolve_backend_url("minio", 9000),
                "LISTEN_PORT": "9100",
            },
            attach_internet=True,
        )
        self.minio_proxy_url = "http://minio-proxy:9100"
        logger.info("minio_proxy_started", upstream=_resolve_backend_url("minio", 9000))

        await self._launch(
            name="presign-proxy",
            env={
                "PROXY_TYPE": "presign",
                "UPSTREAM_URL": _resolve_backend_url("orchestrator", 8000),
                "LISTEN_PORT": "9104",
            },
            attach_internet=True,
        )
        self.presign_proxy_url = "http://presign-proxy:9104"
        logger.info("presign_proxy_started", upstream=_resolve_backend_url("orchestrator", 8000))

        if anthropic_api_key and enable_auth_proxy:
            await self._launch(
                name="auth-proxy",
                env={
                    "PROXY_TYPE": "auth",
                    "ANTHROPIC_API_KEY": anthropic_api_key,
                    "LISTEN_PORT": "9100",
                    "PROXY_ADMIN_TOKEN": self._proxy_admin_token,
                },
                attach_internet=True,
            )
            self.auth_proxy_url = "http://auth-proxy:9100"
            logger.info("auth_proxy_started")
        elif anthropic_api_key and not enable_auth_proxy:
            logger.info("auth_proxy_skipped", reason="AUTH_PROXY_ENABLED=false")

        # EMB-45: xai-proxy holds no static credential — the SuperGrok access token
        # is pushed at runtime via push_xai_token(). Attaches to sandbox-internet to
        # reach api.x.ai. The refresh-token lineage lives orchestrator-side.
        if enable_xai_proxy:
            await self._launch(
                name="xai-proxy",
                env={
                    "PROXY_TYPE": "xai",
                    "LISTEN_PORT": "9106",
                    "PROXY_ADMIN_TOKEN": self._proxy_admin_token,
                },
                attach_internet=True,
            )
            self.xai_proxy_url = "http://xai-proxy:9106"
            logger.info("xai_proxy_started")

        # RAV-1140: context-proxy — mediated log drill-down for watcher analysis
        # jobs. Gated on the flag AND a non-empty container allowlist: with no
        # allowlist there is nothing it could legally serve, so it fails closed
        # by simply not existing (init_node then finds context_proxy_url == ""
        # and the watcher job degrades to briefing-only).
        allowlist = ",".join(c.strip() for c in context_container_allowlist.split(",") if c.strip())
        if enable_context_proxy and allowlist:
            await self._launch(
                name="context-proxy",
                env={
                    "PROXY_TYPE": "context",
                    "UPSTREAM_URL": _resolve_backend_url("orchestrator", 8000),
                    "ORCHESTRATOR_API_KEY": orchestrator_api_key,
                    "CONTAINER_ALLOWLIST": allowlist,
                    "QUERY_QUOTA": str(context_query_quota),
                    # #294: the operator's PII field names, redacted in results.
                    "LOG_REDACTION_FIELDS": log_redaction_fields,
                    "LISTEN_PORT": "9107",
                    "PROXY_ADMIN_TOKEN": self._proxy_admin_token,
                },
                attach_internet=True,
            )
            self.context_proxy_url = "http://context-proxy:9107"
            logger.info("context_proxy_started", allowlist=allowlist)
        elif enable_context_proxy:
            logger.info("context_proxy_skipped", reason="CONTEXT_PROXY_CONTAINER_ALLOWLIST is empty")

        # The operator's own model server, reached through one proxy instead of
        # an egress exception that would open every port of its machine. It
        # sits on sandbox-internet only for the route out of DinD; sandboxes
        # reach it on sandbox-restricted, and only the ones enrolled for it.
        if local_llm_upstream:
            local_env = {
                "PROXY_TYPE": "local_llm",
                "UPSTREAM_URL": resolve_upstream_url(local_llm_upstream),
                "UPSTREAM_API_KEY": local_llm_api_key,
                "LISTEN_PORT": "9108",
                "PROXY_ADMIN_TOKEN": self._proxy_admin_token,
            }
            if local_llm_max_request_bytes > 0:
                local_env["MAX_REQUEST_BYTES"] = str(local_llm_max_request_bytes)
            await self._launch(name="local-llm-proxy", env=local_env, attach_internet=True)
            self.local_llm_proxy_url = "http://local-llm-proxy:9108"
            logger.info("local_llm_proxy_started")

        # Only leftovers we did not just launch. A name in ``_launched`` is the
        # live shared proxy; the sweep must not remove it.
        self._pending_removal -= set(self._launched)
        self._ensure_sweep()
        logger.info("proxy_manager_started", launched=list(self._launched))

    async def _launch(self, *, name: str, env: dict[str, str], attach_internet: bool) -> None:
        import os

        run_cmd, env_file_path = self._docker.build_run_proxy_cmd(
            name=name,
            image=self._image,
            network=_RESTRICTED,
            env=env,
        )
        try:
            await self._docker.run_cmd(run_cmd)
        finally:
            try:
                os.unlink(env_file_path)
            except OSError:
                logger.warning("proxy_env_file_unlink_failed", path=env_file_path)
        if name not in self._launched:
            self._launched.append(name)
        self._launch_specs[name] = (dict(env), attach_internet)
        self._pending_removal.discard(name)
        if attach_internet:
            connect_cmd = self._docker.build_network_cmd("connect", _INTERNET, name)
            await self._docker.run_cmd(connect_cmd)
            if name not in self._internet_attached:
                self._internet_attached.append(name)

    def on_proxy_recreated(self, hook: Callable[[], Awaitable[None]]) -> None:
        """Run ``hook`` after a proxy found stopped was recreated (#626).

        A recreated proxy may have new addresses, and the rules that let
        sandboxes reach a proxy name its addresses; production passes
        ``SandboxManager.refresh_proxy_policy``.
        """
        self._on_recreated = hook

    def internet_proxies(self) -> list[str]:
        """Names of the running proxies attached to ``sandbox-internet``."""
        return list(self._internet_attached)

    def launched_proxies(self) -> list[str]:
        """Names of the running proxies. Every one is on ``sandbox-restricted``."""
        return list(self._launched)

    async def enroll_sandbox(
        self,
        sandbox_id: str,
        *,
        github_token: str | None = None,
        git_host: str | None = None,
        git_username: str | None = None,
        forge: str | None = None,
        xai_credential_id: str | None = None,
        repo: str | None = None,
        git_upstream: str | None = None,
        git_push: bool = False,
        git_push_branches: Sequence[str] = (),
        job_id: str | None = None,
        local_llm: bool = False,
    ) -> str:
        """Enroll a new sandbox with all running credential proxies.

        Returns the bearer token to plumb into the sandbox.

        The orchestrator cannot reach the proxies directly via HTTP because the
        proxies live on DinD's ``sandbox-restricted`` network, which is
        invisible from the host backend network where the orchestrator runs.
        Instead we ``docker exec`` a tiny Python urllib request inside each
        proxy container — DinD's daemon DOES have a path to its own
        sandbox-restricted, and the proxy listens on 0.0.0.0 so localhost
        works. Phase 1.5 fix.

        Network-plumbing proxies (minio, presign) have no admin endpoints and
        require no enrollment.

        ``github_token``, when provided, is included in the enroll body so the
        proxies can store a per-sandbox (per-owner) GitHub token instead of
        falling back to the global token. Omitted (falsy) means back-compat:
        the proxies keep using the global token.

        ``forge`` (``github``/``gitlab``) limits that payload to git-proxy and the forge's own API proxy; None broadcasts it; any other value raises ValueError.

        ``repo`` scopes the forge API proxies (#520): the proxy of the job's
        forge forwards read calls on that repository only, and the other one
        (or both, with no repo) is enrolled with an empty scope and forwards
        nothing. A proxy that does not echo the scope it was given runs an
        image older than scoping and would forward anything, so the sandbox
        is unenrolled from that proxy again and the job carries on without it:
        no sandbox-side code calls these proxies.

        ``repo``, ``git_upstream`` and ``git_push`` are the git-proxy's grant
        (#521): it forwards git's smart HTTP for ``repo`` to ``git_upstream``
        (the forge base URL the sandbox clones from), and pushes only with
        ``git_push``. Without a repo or an upstream the grant is empty and the
        sandbox reaches no repository through it. A git-proxy that does not
        echo the grant runs an image older than forwarding, which hands the
        forge token to any holder of the bearer: the sandbox is unenrolled
        there and RuntimeError is raised, so the sandbox is never used.

        ``git_push_branches`` are the branches a push may create or update
        (#559, ADR 0009); the git-proxy refuses every other ref update and
        every deletion. They are sent only with ``git_push``, and a git-proxy
        that does not echo them runs an image older than ref scoping, which
        would let the push touch any ref: refused the same way. ``job_id``
        goes to the git-proxy so its refusals name the job.

        ``local_llm`` also enrolls the sandbox with the local-llm-proxy: only a
        sandbox whose job resolved the operator's model server asks for it.

        Raises RuntimeError if any enrollment exec fails, if such a stale
        proxy cannot be revoked, or if the git-proxy does not confirm its
        grant. Caller (SandboxManager) rolls back the just-created container
        in that case.
        """
        if self._http is None:
            raise RuntimeError("ProxyManager.start() must be called before enroll_sandbox")
        if forge is not None and forge not in ("github", "gitlab"):
            # Fail closed: an unroutable forge must not broadcast the credential.
            raise ValueError(f"unknown forge {forge!r}")

        sandbox_token = secrets.token_urlsafe(32)
        base: dict[str, Any] = {
            "sandbox_id": sandbox_id,
            "sandbox_token": sandbox_token,
            "ttl_seconds": self._enroll_ttl_seconds,
        }
        payload: dict[str, Any] = {}
        if github_token:
            payload["github_token"] = github_token
        # Per-sandbox git identity (#166): the username the git-proxy
        # authenticates as, and the host its credential is for. The proxy
        # sends the credential to no other host (#521). A sandbox works on
        # exactly one repo, so the forge is known here.
        if git_host:
            payload["git_host"] = git_host
            payload["git_username"] = git_username or "oauth2"

        # Spec 2026-09-29 §3.2: a forge payload goes only to git-proxy and that
        # forge's API proxy. Before this, a GitLab token landed in github-proxy
        # and vice versa. forge=None keeps the old broadcast for callers that
        # predate the registry.
        forge_targets = {"github": {"git-proxy", "github-proxy"}, "gitlab": {"git-proxy", "gitlab-proxy"}}.get(
            forge or ""
        )  # None only when forge is None (unknown values raised above)

        targets: list[tuple[str, int]] = []
        if self.git_proxy_url:
            targets.append(("git-proxy", 9101))
        if self.github_proxy_url:
            targets.append(("github-proxy", 9103))
        if self.gitlab_proxy_url:
            targets.append(("gitlab-proxy", 9105))
        if self.auth_proxy_url:
            targets.append(("auth-proxy", 9100))
        if self.xai_proxy_url:
            targets.append(("xai-proxy", 9106))
        if self.local_llm_proxy_url and local_llm:
            targets.append(("local-llm-proxy", 9108))
        # context-proxy is deliberately ABSENT from this broadcast: log access is
        # per-job opt-in (watcher analysis jobs only). init_node calls
        # enroll_context() explicitly when the job carries the watcher flag —
        # a sandbox never enrolled here dead-ends at the proxy's 401 (RAV-1140).

        git_grant = self._git_grant(repo, git_upstream, git_push, git_push_branches)
        for name, port in targets:
            body = dict(base)
            if forge_targets is None or name in forge_targets:
                body.update(payload)
            # The credential id is not a secret. Only the xai-proxy needs it,
            # and only when this sandbox resolved an oauth account.
            if name == "xai-proxy" and xai_credential_id:
                body["xai_credential_id"] = xai_credential_id
            scope = self._repo_scope(name, repo, forge)
            if scope is not None:
                body[SCOPE_FIELD] = scope
            if name == "git-proxy":
                body.update(git_grant)
                if job_id:
                    body[JOB_FIELD] = job_id
            response = await self._exec_admin_request(
                container=name,
                method="POST",
                path="/admin/enroll",
                port=port,
                body=body,
            )
            if scope is not None:
                await self._confirm_repo_scope(name, port, sandbox_id, scope, response)
            if name == "git-proxy":
                await self._confirm_git_grant(port, sandbox_id, git_grant, response)

        if xai_credential_id:
            logger.info("sandbox_xai_credential", sandbox_id=sandbox_id, credential_id=xai_credential_id)
        logger.info(
            "sandbox_enrolled_with_proxies",
            sandbox_id=sandbox_id,
            proxies=[t[0] for t in targets],
        )
        return sandbox_token

    @staticmethod
    def _repo_scope(proxy: str, repo: str | None, forge: str | None) -> list[str] | None:
        """``allowed_repos`` for one proxy's enroll body; None for a proxy that takes no scope."""
        spec = _FORGE_API_PROXIES.get(proxy)
        if spec is None:
            return None
        proxy_forge, normalize = spec
        if not repo or (forge is not None and forge != proxy_forge):
            return []
        normalized = normalize(repo)
        if normalized is None:
            # Enrolling with it would get a 400 and fail the sandbox. An empty
            # scope only loses API calls that no sandbox code makes.
            logger.warning("forge_api_scope_repo_invalid", proxy=proxy, repo=repo[:200])
            return []
        return [normalized]

    @staticmethod
    def _git_grant(
        repo: str | None, upstream: str | None, push: bool, push_branches: Sequence[str] = ()
    ) -> dict[str, Any]:
        """The git-proxy's enroll-body grant: ``allowed_repos``, ``git_upstream``, ``git_push``
        and, with ``git_push``, ``git_push_branches``."""
        grant: dict[str, Any] = {SCOPE_FIELD: [], PUSH_FIELD: bool(push)}
        if push:
            grant[PUSH_BRANCHES_FIELD] = _push_branches(push_branches)
        normalized_upstream = normalize_upstream(upstream) if upstream else None
        if upstream and normalized_upstream is None:
            logger.warning("git_proxy_upstream_invalid", upstream=upstream[:200])
        if normalized_upstream:
            grant[UPSTREAM_FIELD] = normalized_upstream
        normalized_repo = normalize_git_repo(repo) if repo else None
        if repo and normalized_repo is None:
            logger.warning("git_proxy_scope_repo_invalid", repo=repo[:200])
        if normalized_repo and normalized_upstream:
            grant[SCOPE_FIELD] = [normalized_repo]
        return grant

    async def _confirm_git_grant(
        self, port: int, sandbox_id: str, grant: dict[str, Any], response: dict[str, Any]
    ) -> None:
        """Refuse a git-proxy that did not echo ``grant``: its image hands the token to the sandbox."""
        echoed = response if isinstance(response, dict) else {}
        confirmed = (
            isinstance(echoed.get(SCOPE_FIELD), list)
            and sorted(str(r) for r in echoed[SCOPE_FIELD]) == sorted(grant[SCOPE_FIELD])
            and echoed.get(PUSH_FIELD) is grant[PUSH_FIELD]
            and echoed.get(UPSTREAM_FIELD, "") == grant.get(UPSTREAM_FIELD, "")
            and (
                PUSH_BRANCHES_FIELD not in grant
                or (
                    isinstance(echoed.get(PUSH_BRANCHES_FIELD), list)
                    and sorted(str(b) for b in echoed[PUSH_BRANCHES_FIELD]) == grant[PUSH_BRANCHES_FIELD]
                )
            )
        )
        if confirmed:
            return
        logger.error(
            "git_proxy_grant_unconfirmed",
            sandbox_id=sandbox_id,
            detail=(
                "the git-proxy did not confirm the repository grant: its image predates smart-HTTP "
                "forwarding (it would hand the forge token to the sandbox) or push-branch scoping (it "
                "would let a push touch any ref), so the sandbox is unenrolled there and refused; "
                "rebuild the proxy image"
            ),
        )
        await self._exec_admin_request(
            container="git-proxy",
            method="DELETE",
            path=f"/admin/enroll/{sandbox_id}",
            port=port,
            body=None,
            accept_404=True,
        )
        raise RuntimeError("git-proxy did not confirm the repository grant (proxy image older than the orchestrator?)")

    async def set_git_push_branches(self, sandbox_id: str, branches: Sequence[str]) -> None:
        """Replace the branches an enrolled sandbox may push (#559): the developer's chosen branch.

        Raises RuntimeError when a branch is not one the git-proxy accepts,
        when the git-proxy is not running, refuses, or does not echo exactly
        ``branches`` (an image older than the route answers
        404 or 405). The sandbox keeps the branches it had, so a failure never
        widens what it may push.
        """
        if not self.git_proxy_url:
            raise RuntimeError("git-proxy is not running")
        wanted = _push_branches(branches)
        if not wanted or len(wanted) != len(set(branches)):
            raise RuntimeError(f"not branches the git-proxy accepts: {sorted(branches)!r}")
        response = await self._exec_admin_request(
            container="git-proxy",
            method="PUT",
            path=f"/admin/git-push-branches/{sandbox_id}",
            port=9101,
            body={PUSH_BRANCHES_FIELD: wanted},
        )
        echoed = response.get(PUSH_BRANCHES_FIELD) if isinstance(response, dict) else None
        if not isinstance(echoed, list) or sorted(str(b) for b in echoed) != wanted:
            raise RuntimeError("git-proxy did not confirm the push branches (proxy image older than the orchestrator?)")
        # A recreated git-proxy is given the held enroll body back (#626): it
        # must carry the branches confirmed now, not the create-time ones.
        # Recorded only once confirmed, so a replay never grants more than the
        # proxy accepted.
        held = self._admin_state.get("git-proxy", {}).get(f"enroll:{sandbox_id}")
        if held is not None:
            path, port, body, expires = held
            self._admin_state["git-proxy"][f"enroll:{sandbox_id}"] = (
                path,
                port,
                {**body, PUSH_BRANCHES_FIELD: wanted},
                expires,
            )
        logger.info("sandbox_git_push_branches_set", sandbox_id=sandbox_id, branches=wanted)

    async def _confirm_repo_scope(
        self, proxy: str, port: int, sandbox_id: str, scope: list[str], response: dict[str, Any]
    ) -> None:
        """Revoke the enrollment on a proxy that did not echo ``scope`` (an image older than scoping)."""
        granted = response.get(SCOPE_FIELD) if isinstance(response, dict) else None
        if isinstance(granted, list) and sorted(str(g) for g in granted) == sorted(scope):
            return
        logger.error(
            "forge_api_proxy_scope_unconfirmed",
            proxy=proxy,
            sandbox_id=sandbox_id,
            detail=(
                f"the proxy did not confirm {SCOPE_FIELD}: its image predates per-repository scoping and "
                "would forward any call, so this sandbox is unenrolled there; rebuild the proxy image"
            ),
        )
        await self._exec_admin_request(
            container=proxy,
            method="DELETE",
            path=f"/admin/enroll/{sandbox_id}",
            port=port,
            body=None,
            accept_404=True,
        )

    async def enroll_context(
        self,
        sandbox_id: str,
        sandbox_token: str,
        *,
        containers: list[str] | None = None,
    ) -> list[str] | None:
        """Enroll a sandbox with the context-proxy (RAV-1140, per-job opt-in).

        Called by init_node with the sandbox_token already minted by
        enroll_sandbox — the sandbox presents one bearer everywhere. Raises
        RuntimeError if the proxy isn't running or the enrollment exec fails;
        the caller degrades the job to briefing-only rather than failing it.

        ``containers=None`` is the watcher's analysis job: the proxy's full
        allowlist. A list (SH-P4.4, developer jobs) scopes the sandbox to those
        containers; the proxy narrows it to its allowlist and echoes what it
        granted, which is returned. A proxy that does NOT echo a scope is an
        image built before scoping existed — it has just granted the full
        allowlist, so the sandbox is unenrolled again and RuntimeError raised.
        """
        if containers is not None and not containers:
            raise ValueError("containers is empty — pass None for an unscoped enrollment")
        if not self.context_proxy_url:
            raise RuntimeError("context-proxy is not running")
        body: dict[str, Any] = {
            "sandbox_id": sandbox_id,
            "sandbox_token": sandbox_token,
            "ttl_seconds": self._enroll_ttl_seconds,
        }
        if containers is not None:
            body["allowed_containers"] = list(containers)
        response = await self._exec_admin_request(
            container="context-proxy",
            method="POST",
            path="/admin/enroll",
            port=9107,
            body=body,
        )
        if containers is None:
            logger.info("sandbox_enrolled_with_context_proxy", sandbox_id=sandbox_id)
            return None
        granted = (response or {}).get("allowed_containers")
        if not isinstance(granted, list) or not granted or not set(granted) <= set(containers):
            await self._exec_admin_request(
                container="context-proxy",
                method="DELETE",
                path=f"/admin/enroll/{sandbox_id}",
                port=9107,
                body=None,
                accept_404=True,
            )
            raise RuntimeError(
                "context-proxy did not confirm the container scope (proxy image predates scoped enrollment?)"
            )
        logger.info("sandbox_enrolled_with_context_proxy", sandbox_id=sandbox_id, containers=granted)
        return [str(c) for c in granted]

    async def unenroll_sandbox(self, sandbox_id: str) -> None:
        """Authoritative unenrollment (RAV-1070): retried per proxy, never raises.

        Each proxy gets up to three attempts with a short backoff; a proxy that
        still fails is logged at ERROR — its bearer stays live only until the
        enrollment TTL kills it (the fail-safe this retry loop backs onto).
        """
        if self._http is None:
            return
        targets: list[tuple[str, int]] = []
        if self.git_proxy_url:
            targets.append(("git-proxy", 9101))
        if self.github_proxy_url:
            targets.append(("github-proxy", 9103))
        if self.gitlab_proxy_url:
            targets.append(("gitlab-proxy", 9105))
        if self.auth_proxy_url:
            targets.append(("auth-proxy", 9100))
        if self.xai_proxy_url:
            targets.append(("xai-proxy", 9106))
        if self.context_proxy_url:
            # In unenroll (unlike enroll) the context-proxy IS in the sweep:
            # accept_404 tolerates the common never-enrolled case, and a
            # watcher-enrolled sandbox must be revoked with the rest.
            targets.append(("context-proxy", 9107))
        if self.local_llm_proxy_url:
            # Swept like the context-proxy: accept_404 covers a sandbox never enrolled there.
            targets.append(("local-llm-proxy", 9108))

        for name, port in targets:
            for attempt in range(3):
                try:
                    await self._exec_admin_request(
                        container=name,
                        method="DELETE",
                        path=f"/admin/enroll/{sandbox_id}",
                        port=port,
                        body=None,
                        accept_404=True,
                    )
                    break
                except RuntimeError as exc:
                    if attempt < 2:
                        logger.warning("sandbox_unenroll_retrying", proxy=name, attempt=attempt + 1, error=str(exc))
                        await asyncio.sleep(0.5 * (attempt + 1))
                    else:
                        logger.error(
                            "sandbox_unenroll_failed",
                            proxy=name,
                            sandbox_id=sandbox_id,
                            error=str(exc),
                            ttl_backstop_seconds=self._enroll_ttl_seconds,
                        )

    async def push_xai_token(
        self,
        credential_id: str,
        access_token: str | None = None,
        expires_at: str | None = None,
    ) -> None:
        """Push a SuperGrok access token to the xai-proxy.

        ``push_xai_token(credential_id, access_token, expires_at)`` fills that
        credential's slot. The one-argument form ``push_xai_token(access_token)``
        fills the global holder the file-backed refresher still uses.

        No-op if the xai-proxy isn't running. Raises RuntimeError if the push
        fails so the caller's background loop can log and retry.
        """
        if not self.xai_proxy_url:
            return
        if access_token is None:
            if not credential_id:
                raise RuntimeError("push_xai_token called with an empty access token")
            body: dict[str, Any] = {"access_token": credential_id}
        else:
            if not access_token:
                raise RuntimeError("push_xai_token called with an empty access token")
            body = {
                "credential_id": credential_id,
                "access_token": access_token,
                "expires_at": expires_at,
            }
        await self._exec_admin_request(
            container="xai-proxy",
            method="POST",
            path="/admin/token",
            port=9106,
            body=body,
        )
        logger.info("xai_proxy_token_pushed", credential_id=credential_id if access_token is not None else None)

    async def xai_proxy_health(self) -> dict[str, Any]:
        """Upstream 401/429 counts since the proxy's previous read. The read resets them."""
        if not self.xai_proxy_url:
            return {}
        if self._http is None:
            raise RuntimeError("ProxyManager.start() must be called before xai_proxy_health")
        report = await self._exec_admin_request(
            container="xai-proxy",
            method="GET",
            path="/admin/health",
            port=9106,
            body=None,
        )
        return report if isinstance(report, dict) else {}

    async def _exec_admin_request(
        self,
        *,
        container: str,
        method: str,
        path: str,
        port: int,
        body: dict[str, Any] | None,
        accept_404: bool = False,
    ) -> dict[str, Any]:
        """One admin request to a proxy; a proxy found stopped or missing is recreated first (#626).

        A proxy exits on its own (a crash, an OOM kill, a daemon restart) and
        nothing else starts it again: every enroll and unenroll that needed it
        failed until the next orchestrator start. When ``docker exec`` says the
        container is not running or does not exist, the proxy is recreated as
        ``start()`` launched it, the enrollments and tokens it held are
        replayed, and the request is sent once more. Raises as
        :meth:`_exec_admin_once` does when that fails too.
        """
        kwargs: dict[str, Any] = {
            "container": container,
            "method": method,
            "path": path,
            "port": port,
            "body": body,
            "accept_404": accept_404,
        }
        try:
            response = await self._exec_admin_once(**kwargs)
        except RuntimeError as exc:
            if not _proxy_gone(exc) or not await self._revive(container):
                raise
            response = await self._exec_admin_once(**kwargs)
        self._remember(container, method, path, port, body)
        return response

    def _remember(self, container: str, method: str, path: str, port: int, body: dict[str, Any] | None) -> None:
        """Keep the admin state a proxy holds in memory, so a recreated proxy gets it back."""
        held = self._admin_state.setdefault(container, {})
        now = time.monotonic()
        for key in [k for k, (_, _, _, expires) in held.items() if expires <= now]:
            del held[key]
        if method == "POST" and path == "/admin/enroll" and body and body.get("sandbox_id"):
            ttl = float(body.get("ttl_seconds") or self._enroll_ttl_seconds)
            held[f"enroll:{body['sandbox_id']}"] = (path, port, dict(body), now + ttl)
        elif method == "DELETE" and path.startswith("/admin/enroll/"):
            held.pop(f"enroll:{path.removeprefix('/admin/enroll/')}", None)
        elif method == "POST" and path == "/admin/token" and body:
            held[f"token:{body.get('credential_id') or ''}"] = (path, port, dict(body), float("inf"))

    async def _revive(self, name: str) -> bool:
        """Recreate the stopped proxy ``name`` and replay its admin state. True when it runs again.

        Only a proxy this process launched is recreated, and not while
        stopping. Concurrent callers wait for one recreation.
        """
        if self._stopping or name not in self._launch_specs:
            return False
        async with self._revive_locks.setdefault(name, asyncio.Lock()):
            if await self._is_running(name):
                return True  # another caller recreated it while this one waited
            env, attach_internet = self._launch_specs[name]
            logger.warning("proxy_not_running_recreating", proxy=name)
            await self._remove_proxies([name], phase="revive")
            try:
                await self._launch(name=name, env=env, attach_internet=attach_internet)
            except Exception as exc:  # noqa: BLE001 — the caller gets its original error
                logger.error("proxy_recreate_failed", proxy=name, error=str(exc))
                return False
            restored, failed = await self._replay(name)
            logger.warning("proxy_recreated", proxy=name, restored=restored, failed=failed)
        if self._on_recreated is not None:
            try:
                await self._on_recreated()
            except Exception as exc:  # noqa: BLE001 — the proxy runs; the rules are retried by every create
                logger.error("proxy_recreated_rules_refresh_failed", proxy=name, error=str(exc))
        return True

    async def _is_running(self, name: str) -> bool:
        try:
            out = await self._docker.run_cmd(self._docker.build_inspect_running_cmd(name), timeout=10)
        except Exception:  # noqa: BLE001 — missing or unreadable is not running
            return False
        return str(out).strip() == "true"

    async def _replay(self, name: str) -> tuple[int, int]:
        """Send a recreated proxy the enrollments and tokens it held. Returns (restored, failed).

        An enrollment keeps the expiry it had; one already expired is dropped.
        """
        restored = failed = 0
        held = self._admin_state.get(name, {})
        now = time.monotonic()
        for key, (path, port, body, expires) in list(held.items()):
            if expires <= now:
                del held[key]
                continue
            replay = dict(body)
            if key.startswith("enroll:"):
                replay["ttl_seconds"] = max(1, int(expires - now))
            try:
                await self._exec_admin_once(
                    container=name, method="POST", path=path, port=port, body=replay, accept_404=False
                )
                restored += 1
            except RuntimeError as exc:
                failed += 1
                logger.error("proxy_state_replay_failed", proxy=name, entry=key.split(":", 1)[0], error=str(exc))
        return restored, failed

    async def _exec_admin_once(
        self,
        *,
        container: str,
        method: str,
        path: str,
        port: int,
        body: dict[str, Any] | None,
        accept_404: bool = False,
    ) -> dict[str, Any]:
        """Run a tiny urllib request via ``docker exec <container> python3 -c ...``.

        This is the in-DinD enrollment transport. The proxy container has
        Python 3.12 + stdlib but no curl/wget; urllib.request is sufficient
        for the small JSON POST/DELETE payloads. We pass the admin token via
        environment variable on the exec call (so it never appears in argv
        for any host-side ``ps``).

        Raises RuntimeError on any non-2xx response (or non-2xx + non-404 if
        ``accept_404`` is True). Returns the 2xx response's JSON object, or
        ``{}`` when it has none.
        """
        import json

        payload = json.dumps(body or {})
        # The proxy image (python:3.12-slim) has Python + stdlib but no curl.
        # We pass the admin token + body via -e on the exec so they stay out
        # of argv and any host-side `ps`. The script must be a multi-line
        # block; semicolons can't separate `try`/`except`, so we use '\n'.
        script = "\n".join(
            [
                "import os, sys, urllib.request, urllib.error",
                "tok = os.environ['ADMIN_TOK']",
                "body = os.environ.get('ADMIN_BODY', '').encode()",
                f"req = urllib.request.Request('http://localhost:{port}{path}',",
                "    data=body if body else None,",
                "    headers={'X-Admin-Token': tok, 'Content-Type': 'application/json'},",
                f"    method='{method}')",
                "try:",
                "    resp = urllib.request.urlopen(req, timeout=10)",
                # Body first, marker last: the status is parsed from the final marker.
                "    sys.stdout.write(resp.read(65536).decode('utf-8', 'replace'))",
                "    sys.stdout.write(f'\\nHTTP_CODE={resp.status}')",
                "except urllib.error.HTTPError as e:",
                "    sys.stdout.write(f'HTTP_CODE={e.code}')",
                "except Exception as e:",
                "    sys.stderr.write(f'ERR:{type(e).__name__}:{e}')",
                "    sys.exit(2)",
            ]
        )
        cmd = self._docker.build_exec_cmd(
            container,
            ["python3", "-c", script],
            env={"ADMIN_TOK": self._proxy_admin_token, "ADMIN_BODY": payload},
        )
        try:
            output = await self._docker.run_cmd(cmd, timeout=15)
        except RuntimeError as exc:
            raise RuntimeError(f"docker exec {container} for {method} {path} failed: {exc}") from exc

        # The Python script prints HTTP_CODE=<status> on success, or stderr on
        # failure. run_cmd already raised on non-zero exit — so output here is
        # the status code line.
        marker = "HTTP_CODE="
        idx = output.rfind(marker)
        if idx < 0:
            raise RuntimeError(f"enroll {container} {path}: missing HTTP_CODE marker in output: {output[:200]!r}")
        status = int(output[idx + len(marker) :].strip())
        if 200 <= status < 300:
            try:
                parsed = json.loads(output[:idx])
            except ValueError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        if accept_404 and status == 404:
            return {}
        raise RuntimeError(f"enroll {container} {path} returned HTTP {status}")

    async def stop(self) -> None:
        """Force-remove all launched proxy containers.

        Does not raise. A removal that fails because the daemon is unreachable
        is retried with backoff; the final failure is a structured log carrying
        the container name and phase. Shutdown has no job, so there is no
        trace id to attach — the phase is the correlation. Leftovers are
        removed by the next ``start()`` pre-clean once the daemon is back.
        """
        self._stopping = True
        await self._cancel_sweep()
        failed = await self._remove_proxies(list(self._launched), phase="stop")
        # Record only so a caller that keeps the process up can sweep them.
        # Do not schedule that sweep here: this process is shutting down, and
        # a late ``rm`` could hit a replacement orchestrator's live proxies.
        self._pending_removal = set(failed)
        self._launched.clear()
        self._internet_attached.clear()
        self.git_proxy_url = ""
        self.github_proxy_url = ""
        self.gitlab_proxy_url = ""
        self.auth_proxy_url = ""
        self.xai_proxy_url = ""
        self.minio_proxy_url = ""
        self.presign_proxy_url = ""
        self.context_proxy_url = ""
        self.local_llm_proxy_url = ""
        session = self._http
        self._http = None
        if session is not None:
            try:
                await session.close()
            except Exception:
                # An exception escaping stop() fails the lifespan. Starlette
                # forwards the formatted traceback to uvicorn's stdlib logger,
                # which is an unstructured line rather than a structlog event.
                logger.warning("proxy_http_session_close_failed", exc_info=True)
        logger.info("proxy_manager_stopped")

    async def _remove_proxies(self, names: list[str], *, phase: str) -> list[str]:
        """Force-remove ``names``. Retry only when the daemon is unreachable.

        Returns the names that could not be removed. Never raises (except
        cancellation, which must still abort shutdown).
        """
        pending = list(dict.fromkeys(names))
        failed: list[str] = []
        for attempt in range(1, _CLEANUP_ATTEMPTS + 1):
            if not pending:
                break
            if self._stopping and phase != "stop":
                # stop() owns removal from here. Hand the leftovers back so the
                # caller does not treat an aborted sweep as a successful clear.
                return list(dict.fromkeys([*failed, *pending]))
            if attempt > 1:
                await asyncio.sleep(_CLEANUP_BACKOFF_S[attempt - 2])
            still_unreachable: list[str] = []
            for name in pending:
                try:
                    await self._docker.run_cmd(
                        self._docker.build_rm_cmd(name),
                        timeout=_CLEANUP_RM_TIMEOUT_S,
                        log_context={"container": name, "phase": phase, "attempt": attempt},
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if _daemon_unreachable(exc) and attempt < _CLEANUP_ATTEMPTS:
                        logger.warning(
                            "proxy_container_rm_retrying",
                            container=name,
                            name=name,
                            phase=phase,
                            attempt=attempt,
                            error=str(exc),
                        )
                        still_unreachable.append(name)
                        continue
                    logger.error(
                        "proxy_container_rm_failed",
                        container=name,
                        name=name,
                        phase=phase,
                        attempt=attempt,
                        error=str(exc),
                    )
                    if _daemon_unreachable(exc):
                        failed.append(name)
            pending = still_unreachable
        return failed

    def _ensure_sweep(self) -> None:
        """Schedule a follow-up removal of names this process failed to remove.

        No-op while stopping, or when nothing is pending. The task removes only
        known proxy names that are still pending and not currently launched.
        """
        if self._stopping or not self._pending_removal:
            return
        if self._sweep_task is not None and not self._sweep_task.done():
            return
        self._sweep_task = asyncio.create_task(self._sweep_pending(), name="proxy-cleanup-sweep")

    async def _cancel_sweep(self) -> None:
        task = self._sweep_task
        self._sweep_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _sweep_pending(self) -> None:
        """Retry removals this process already failed, and nothing else."""
        try:
            for delay in _SWEEP_DELAYS_S:
                await asyncio.sleep(delay)
                if self._stopping:
                    return
                targets = [
                    name
                    for name in _PROXY_CONTAINER_NAMES
                    if name in self._pending_removal and name not in self._launched
                ]
                if not targets:
                    self._pending_removal.clear()
                    return
                failed = await self._remove_proxies(targets, phase="delayed_sweep")
                self._pending_removal = {name for name in failed if name not in self._launched}
                if not self._pending_removal:
                    logger.info("proxy_cleanup_sweep_cleared", phase="delayed_sweep")
                    return
            if self._pending_removal and not self._stopping:
                logger.error(
                    "proxy_cleanup_sweep_exhausted",
                    containers=sorted(self._pending_removal),
                    phase="delayed_sweep",
                )
        except asyncio.CancelledError:
            raise


def _push_branches(branches: Sequence[str]) -> list[str]:
    """Sorted, de-duplicated branch names the git-proxy accepts; a name it would refuse is dropped, loudly.

    Dropping it fails closed: the proxy then refuses a push to that branch,
    where sending it would have the whole enrollment refused.
    """
    kept: set[str] = set()
    for branch in branches:
        normalized = normalize_push_branch(branch)
        if normalized is None:
            logger.warning("git_proxy_push_branch_invalid", branch=str(branch)[:200])
            continue
        kept.add(normalized)
    return sorted(kept)
