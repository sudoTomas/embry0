"""Proxy lifecycle manager — launches credential-injecting proxies as DinD containers.

Three stateless proxies (git, github, auth) run as containers on the
sandbox-restricted network so sandboxes can resolve them by Docker DNS.
The github and auth proxies are also attached to sandbox-internet so they
can reach api.github.com and api.anthropic.com respectively.

The per-job Athanor API proxy (CreateIssue / RequestInput / UpdateStatus)
is deferred — it has DB coupling that needs separate rework. The
``start_athanor_proxy_for_job`` method is a no-op stub.
"""

from __future__ import annotations

import secrets
from typing import Any

import aiohttp
import structlog

from athanor.execution.docker_client import DockerClient

logger = structlog.get_logger(__name__)

_IMAGE = "athanor-proxy:latest"
_RESTRICTED = "sandbox-restricted"
_INTERNET = "sandbox-internet"


class ProxyManager:
    """Manages proxy container lifecycle for sandbox agent access."""

    def __init__(self, docker: DockerClient, *, proxy_admin_token: str) -> None:
        if not proxy_admin_token:
            raise ValueError("proxy_admin_token is required")
        self._docker = docker
        self._launched: list[str] = []
        self._proxy_admin_token = proxy_admin_token
        self._http: aiohttp.ClientSession | None = None
        self.auth_proxy_url: str = ""
        self.git_proxy_url: str = ""
        self.github_proxy_url: str = ""
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
        for name in ("git-proxy", "github-proxy", "auth-proxy"):
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
            image=_IMAGE,
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

    async def enroll_sandbox(self, sandbox_id: str) -> str:
        """Enroll a new sandbox with all running proxies. Returns the bearer token.

        Raises RuntimeError if any enrollment HTTP call fails. Caller (SandboxManager)
        rolls back the just-created container in that case.
        """
        if self._http is None:
            raise RuntimeError("ProxyManager.start() must be called before enroll_sandbox")

        sandbox_token = secrets.token_urlsafe(32)
        headers = {"X-Admin-Token": self._proxy_admin_token}
        body = {"sandbox_id": sandbox_id, "sandbox_token": sandbox_token}

        endpoints: list[str] = []
        if self.git_proxy_url:
            endpoints.append(f"{self.git_proxy_url}/admin/enroll")
        if self.github_proxy_url:
            endpoints.append(f"{self.github_proxy_url}/admin/enroll")
        if self.auth_proxy_url:
            endpoints.append(f"{self.auth_proxy_url}/admin/enroll")

        for url in endpoints:
            try:
                async with self._http.post(
                    url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise RuntimeError(f"enroll {url} returned {resp.status}: {text[:200]}")
            except (aiohttp.ClientError, TimeoutError) as exc:
                raise RuntimeError(f"enroll {url} failed: {exc!r}") from exc

        logger.info("sandbox_enrolled_with_proxies", sandbox_id=sandbox_id, endpoints=endpoints)
        return sandbox_token

    async def unenroll_sandbox(self, sandbox_id: str) -> None:
        """Best-effort unenrollment. Failures are logged at warning, never raised."""
        if self._http is None:
            return
        headers = {"X-Admin-Token": self._proxy_admin_token}
        endpoints: list[str] = []
        if self.git_proxy_url:
            endpoints.append(f"{self.git_proxy_url}/admin/enroll/{sandbox_id}")
        if self.github_proxy_url:
            endpoints.append(f"{self.github_proxy_url}/admin/enroll/{sandbox_id}")
        if self.auth_proxy_url:
            endpoints.append(f"{self.auth_proxy_url}/admin/enroll/{sandbox_id}")

        for url in endpoints:
            try:
                async with self._http.delete(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status not in (200, 404):
                        logger.warning("sandbox_unenroll_non_200", url=url, status=resp.status)
            except (aiohttp.ClientError, TimeoutError) as exc:
                logger.warning("sandbox_unenroll_failed", url=url, error=str(exc))

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
        if self._http is not None:
            await self._http.close()
            self._http = None
        logger.info("proxy_manager_stopped")
