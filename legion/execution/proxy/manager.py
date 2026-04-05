"""Proxy service lifecycle manager — starts auth, git, and legion proxies."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from aiohttp import web

logger = structlog.get_logger(__name__)


class ProxyManager:
    """Manages proxy service lifecycle for sandbox agent access."""

    def __init__(self) -> None:
        self._runners: list[web.AppRunner] = []
        self._sites: list[web.TCPSite] = []
        self.auth_proxy_url: str = ""
        self.git_proxy_url: str = ""
        self.legion_proxy_url: str = ""

    async def start(
        self,
        anthropic_api_key: str = "",
        github_token: str = "",
        auth_proxy_port: int = 9100,
        git_proxy_port: int = 9101,
    ) -> None:
        """Start auth and git proxy services."""
        if anthropic_api_key:
            from legion.execution.proxy.auth_proxy import create_auth_proxy_app
            auth_app = create_auth_proxy_app(anthropic_api_key)
            await self._start_app(auth_app, "0.0.0.0", auth_proxy_port)
            self.auth_proxy_url = f"http://host.docker.internal:{auth_proxy_port}"
            logger.info("auth_proxy_started", port=auth_proxy_port)

        if github_token:
            from legion.execution.proxy.git_proxy import create_git_proxy_app
            git_app = create_git_proxy_app(github_token)
            await self._start_app(git_app, "0.0.0.0", git_proxy_port)
            self.git_proxy_url = f"http://host.docker.internal:{git_proxy_port}"
            logger.info("git_proxy_started", port=git_proxy_port)

        logger.info("proxy_manager_started")

    async def start_legion_proxy_for_job(
        self,
        issues_repo: Any,
        inputs_repo: Any,
        issue_id: str,
        job_id: str,
        repo: str | None = None,
        db: Any = None,
        port: int = 9102,
    ) -> str:
        """Start a Legion API proxy scoped to a specific job. Returns the URL."""
        from legion.execution.proxy.legion_proxy import create_legion_proxy_app
        app = create_legion_proxy_app(
            issues_repo=issues_repo, inputs_repo=inputs_repo,
            issue_id=issue_id, job_id=job_id, repo=repo, db=db,
        )
        await self._start_app(app, "0.0.0.0", port)
        url = f"http://host.docker.internal:{port}"
        logger.info("legion_proxy_started_for_job", job_id=job_id, port=port)
        return url

    async def _start_app(self, app: web.Application, host: str, port: int) -> None:
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        self._runners.append(runner)
        self._sites.append(site)

    async def stop(self) -> None:
        for runner in self._runners:
            await runner.cleanup()
        self._runners.clear()
        self._sites.clear()
        logger.info("proxy_manager_stopped")
