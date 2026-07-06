"""Proxy lifecycle manager — launches credential-injecting proxies as DinD containers.

Five stateless proxies run as containers on the sandbox-restricted network
so sandboxes can resolve them by Docker DNS:

Credential-injection proxies (hold orchestrator credentials, attach also to
sandbox-internet to reach external APIs):
- git-proxy     → injects GITHUB_TOKEN into git credential helpers
- github-proxy  → injects GITHUB_TOKEN into REST/GraphQL calls
- auth-proxy    → injects ANTHROPIC_API_KEY (currently dead path)

Network-plumbing proxies (Phase 1.5 — sandboxes inside DinD cannot reach
host backend services because DinD has its own daemon and DNS namespace;
these proxies live on sandbox-internet AND sandbox-restricted, holding the
host-side IPs of minio and orchestrator):
- minio-proxy   → forwards to host minio:9000 (S3 API for QA artifacts)
- presign-proxy → forwards to host orchestrator:8000 (POST /internal/qa/presign)

The per-job Athanor API proxy (CreateIssue / RequestInput / UpdateStatus)
is deferred — it has DB coupling that needs separate rework. The
``start_athanor_proxy_for_job`` method is a no-op stub.
"""

from __future__ import annotations

import secrets
import socket
from typing import Any

import aiohttp
import structlog

from athanor.execution.docker_client import DockerClient
from athanor.execution.image_registry import qualify_image

logger = structlog.get_logger(__name__)

_BASE_IMAGE = "athanor-proxy:latest"
_RESTRICTED = "sandbox-restricted"
_INTERNET = "sandbox-internet"


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
    ) -> None:
        if not proxy_admin_token:
            raise ValueError("proxy_admin_token is required")
        self._docker = docker
        self._launched: list[str] = []
        self._proxy_admin_token = proxy_admin_token
        self._image = qualify_image(_BASE_IMAGE, image_registry)
        self._http: aiohttp.ClientSession | None = None
        self.auth_proxy_url: str = ""
        self.git_proxy_url: str = ""
        self.github_proxy_url: str = ""
        self.minio_proxy_url: str = ""
        self.presign_proxy_url: str = ""
        self.athanor_proxy_url: str = ""  # Per-job — set by start_athanor_proxy_for_job (currently no-op)

    async def start(
        self,
        anthropic_api_key: str = "",
        github_token: str = "",
        enable_auth_proxy: bool = False,
    ) -> None:
        """Start the credential proxies as DinD containers.

        The auth-proxy is only launched when ``enable_auth_proxy`` is true AND
        a key is provided. Defaults False because no consumer is wired (the
        Claude Agent SDK currently reads the OAuth token directly via env, not
        through a proxy).

        Idempotent: any pre-existing containers with the same names are removed first.
        """
        if self._http is None:
            self._http = aiohttp.ClientSession()

        # Clean up stragglers from a prior orchestrator run.
        for name in ("git-proxy", "github-proxy", "auth-proxy", "minio-proxy", "presign-proxy"):
            try:
                await self._docker.run_cmd(self._docker.build_rm_cmd(name))
            except RuntimeError:
                pass  # Container didn't exist — fine.

        if github_token:
            await self._launch(
                name="git-proxy",
                env={
                    "PROXY_TYPE": "git",
                    "GITHUB_TOKEN": github_token,
                    "LISTEN_PORT": "9101",
                    "PROXY_ADMIN_TOKEN": self._proxy_admin_token,
                },
                attach_internet=False,
            )
            self.git_proxy_url = "http://git-proxy:9101"
            logger.info("git_proxy_started")

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
        self._launched.append(name)
        if attach_internet:
            connect_cmd = self._docker.build_network_cmd("connect", _INTERNET, name)
            await self._docker.run_cmd(connect_cmd)

    async def start_athanor_proxy_for_job(
        self,
        issues_repo: Any,
        inputs_repo: Any,
        issue_id: str,
        job_id: str,
        repo: str | None = None,
        db: Any = None,
        port: int = 9102,
    ) -> str:
        """Per-job Athanor API proxy — DEFERRED. See docs/architecture.md.

        Workflows that depend on CreateIssue / RequestInput / UpdateStatus
        agent tools must not be registered until this is implemented.
        Workflow registration validation rejects such templates; if a caller
        somehow reaches this method, raise loudly rather than return ``""``.
        """
        raise NotImplementedError(
            "Athanor API proxy is deferred — see docs/architecture.md § Athanor "
            "API Proxy. Workflows depending on CreateIssue/RequestInput/UpdateStatus "
            "must not be registered yet."
        )

    async def enroll_sandbox(self, sandbox_id: str, *, github_token: str | None = None) -> str:
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

        Raises RuntimeError if any enrollment exec fails. Caller
        (SandboxManager) rolls back the just-created container in that case.
        """
        if self._http is None:
            raise RuntimeError("ProxyManager.start() must be called before enroll_sandbox")

        sandbox_token = secrets.token_urlsafe(32)
        body: dict[str, str] = {"sandbox_id": sandbox_id, "sandbox_token": sandbox_token}
        if github_token:
            body["github_token"] = github_token

        targets: list[tuple[str, int]] = []
        if self.git_proxy_url:
            targets.append(("git-proxy", 9101))
        if self.github_proxy_url:
            targets.append(("github-proxy", 9103))
        if self.auth_proxy_url:
            targets.append(("auth-proxy", 9100))

        for name, port in targets:
            await self._exec_admin_request(
                container=name,
                method="POST",
                path="/admin/enroll",
                port=port,
                body=body,
            )

        logger.info(
            "sandbox_enrolled_with_proxies",
            sandbox_id=sandbox_id,
            proxies=[t[0] for t in targets],
        )
        return sandbox_token

    async def unenroll_sandbox(self, sandbox_id: str) -> None:
        """Best-effort unenrollment. Failures are logged at warning, never raised."""
        if self._http is None:
            return
        targets: list[tuple[str, int]] = []
        if self.git_proxy_url:
            targets.append(("git-proxy", 9101))
        if self.github_proxy_url:
            targets.append(("github-proxy", 9103))
        if self.auth_proxy_url:
            targets.append(("auth-proxy", 9100))

        for name, port in targets:
            try:
                await self._exec_admin_request(
                    container=name,
                    method="DELETE",
                    path=f"/admin/enroll/{sandbox_id}",
                    port=port,
                    body=None,
                    accept_404=True,
                )
            except RuntimeError as exc:
                logger.warning("sandbox_unenroll_failed", proxy=name, error=str(exc))

    async def _exec_admin_request(
        self,
        *,
        container: str,
        method: str,
        path: str,
        port: int,
        body: dict[str, Any] | None,
        accept_404: bool = False,
    ) -> None:
        """Run a tiny urllib request via ``docker exec <container> python3 -c ...``.

        This is the in-DinD enrollment transport. The proxy container has
        Python 3.12 + stdlib but no curl/wget; urllib.request is sufficient
        for the small JSON POST/DELETE payloads. We pass the admin token via
        environment variable on the exec call (so it never appears in argv
        for any host-side ``ps``).

        Raises RuntimeError on any non-2xx response (or non-2xx + non-404 if
        ``accept_404`` is True).
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
                "    sys.stdout.write(f'HTTP_CODE={resp.status}')",
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
            return
        if accept_404 and status == 404:
            return
        raise RuntimeError(f"enroll {container} {path} returned HTTP {status}")

    async def stop(self) -> None:
        """Force-remove all launched proxy containers."""
        for name in self._launched:
            try:
                await self._docker.run_cmd(self._docker.build_rm_cmd(name))
            except RuntimeError as exc:
                logger.warning("proxy_container_rm_failed", name=name, error=str(exc))
        self._launched.clear()
        self.git_proxy_url = ""
        self.github_proxy_url = ""
        self.auth_proxy_url = ""
        self.minio_proxy_url = ""
        self.presign_proxy_url = ""
        if self._http is not None:
            await self._http.close()
            self._http = None
        logger.info("proxy_manager_stopped")
