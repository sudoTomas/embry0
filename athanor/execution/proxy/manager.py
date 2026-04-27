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

from typing import Any

import structlog

from athanor.execution.docker_client import DockerClient

logger = structlog.get_logger(__name__)

_IMAGE = "athanor-proxy:latest"
_RESTRICTED = "sandbox-restricted"
_INTERNET = "sandbox-internet"


class ProxyManager:
    """Manages proxy container lifecycle for sandbox agent access."""

    def __init__(self, docker: DockerClient) -> None:
        self._docker = docker
        self._launched: list[str] = []
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
        # Clean up stragglers from a prior orchestrator run.
        for name in ("git-proxy", "github-proxy", "auth-proxy"):
            try:
                await self._docker.run_cmd(self._docker.build_rm_cmd(name))
            except RuntimeError:
                pass  # Container didn't exist — fine.

        if github_token:
            await self._launch(
                name="git-proxy",
                env={"PROXY_TYPE": "git", "GITHUB_TOKEN": github_token, "LISTEN_PORT": "9101"},
                attach_internet=False,
            )
            self.git_proxy_url = "http://git-proxy:9101"
            logger.info("git_proxy_started")

            await self._launch(
                name="github-proxy",
                env={"PROXY_TYPE": "github", "GITHUB_TOKEN": github_token, "LISTEN_PORT": "9103"},
                attach_internet=True,
            )
            self.github_proxy_url = "http://github-proxy:9103"
            logger.info("github_proxy_started")

        if anthropic_api_key and enable_auth_proxy:
            await self._launch(
                name="auth-proxy",
                env={"PROXY_TYPE": "auth", "ANTHROPIC_API_KEY": anthropic_api_key, "LISTEN_PORT": "9100"},
                attach_internet=True,
            )
            self.auth_proxy_url = "http://auth-proxy:9100"
            logger.info("auth_proxy_started")
        elif anthropic_api_key and not enable_auth_proxy:
            logger.info("auth_proxy_skipped", reason="AUTH_PROXY_ENABLED=false")

        logger.info("proxy_manager_started", launched=list(self._launched))

    async def _launch(self, *, name: str, env: dict[str, str], attach_internet: bool) -> None:
        run_cmd = self._docker.build_run_proxy_cmd(
            name=name,
            image=_IMAGE,
            network=_RESTRICTED,
            env=env,
        )
        await self._docker.run_cmd(run_cmd)
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
        """[DEFERRED] Per-job Athanor API proxy is not yet running in DinD.

        Workflows that depend on CreateIssue / RequestInput tools will not
        receive a working proxy URL until this is reworked. Tracked separately.
        """
        logger.warning(
            "athanor_proxy_for_job_not_implemented",
            job_id=job_id,
            issue_id=issue_id,
            msg="Per-job Athanor API proxy is deferred. CreateIssue/RequestInput tools unavailable.",
        )
        self.athanor_proxy_url = ""
        return ""

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
        logger.info("proxy_manager_stopped")
