"""Sandbox container lifecycle management via DinD.

Handles: create (persistent container), exec (agent runs), destroy (cleanup).
No customer code on the host — sandbox clones repo internally.
"""

from typing import Any

import structlog

from athanor.execution.docker_client import DockerClient

logger = structlog.get_logger(__name__)

_DEFAULT_PROFILE: dict[str, Any] = {
    "base_image": "legion-sandbox:latest",
    "memory": "8g",
    "cpus": "4",
    "pids_limit": 256,
    "read_only_root": False,  # Claude CLI needs writable rootfs for Node.js cache
    "cap_drop": ["ALL"],
    "cap_add": [],
    "security_opt": ["no-new-privileges"],
    "default_network": "sandbox-restricted",
    "container_timeout_seconds": 3600,
}


class SandboxManager:
    """Manages sandbox container lifecycle for job execution."""

    def __init__(self, docker: DockerClient) -> None:
        self._docker = docker

    async def create(
        self,
        job_id: str,
        profile: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        """Create a persistent sandbox container. Returns container ID."""
        p = {**_DEFAULT_PROFILE, **(profile or {})}
        name = f"sandbox-{job_id}"

        # Inject credentials into sandbox env
        env = dict(env) if env else {}
        oauth_token = self._read_oauth_token()
        if oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token

        cmd = self._docker.build_run_cmd(
            image=p.get("base_image", _DEFAULT_PROFILE["base_image"]),
            name=name,
            network=p.get("default_network", _DEFAULT_PROFILE["default_network"]),
            memory=p.get("memory", _DEFAULT_PROFILE["memory"]),
            cpus=p.get("cpus", _DEFAULT_PROFILE["cpus"]),
            pids_limit=p.get("pids_limit", _DEFAULT_PROFILE["pids_limit"]),
            cap_drop=p.get("cap_drop", _DEFAULT_PROFILE["cap_drop"]),
            cap_add=p.get("cap_add", _DEFAULT_PROFILE["cap_add"]),
            security_opt=p.get("security_opt", _DEFAULT_PROFILE["security_opt"]),
            read_only=p.get("read_only_root", _DEFAULT_PROFILE["read_only_root"]),
            env=env,
        )
        container_id = await self._docker.run_cmd(cmd)
        logger.info("sandbox_created", job_id=job_id, container=name, image=p.get("base_image"))
        return container_id

    @staticmethod
    def _read_oauth_token() -> str | None:
        """Read Claude OAuth token from host credentials file."""
        import json
        import os

        creds_path = "/home/orchestrator/.claude/.credentials.json"
        if not os.path.isfile(creds_path):
            logger.warning("claude_credentials_not_found", path=creds_path)
            return None
        try:
            with open(creds_path) as f:
                creds = json.load(f)
            token = creds.get("claudeAiOauth", {}).get("accessToken")
            if token:
                logger.info("claude_oauth_token_loaded")
            return token
        except Exception as exc:
            logger.warning("claude_credentials_read_failed", error=str(exc))
            return None

    async def destroy(self, container: str, timeout: int = 10) -> None:
        """Stop and remove a sandbox container."""
        try:
            stop_cmd = self._docker.build_stop_cmd(container, timeout=timeout)
            await self._docker.run_cmd(stop_cmd)
        except RuntimeError:
            logger.warning("sandbox_stop_failed", container=container)

        try:
            rm_cmd = self._docker.build_rm_cmd(container)
            await self._docker.run_cmd(rm_cmd)
        except RuntimeError:
            logger.warning("sandbox_rm_failed", container=container)

        logger.info("sandbox_destroyed", container=container)

    async def connect_network(self, container: str, network: str) -> None:
        """Connect sandbox to an additional network."""
        cmd = self._docker.build_network_cmd("connect", network, container)
        await self._docker.run_cmd(cmd)
        logger.info("sandbox_network_connected", container=container, network=network)

    async def disconnect_network(self, container: str, network: str) -> None:
        """Disconnect sandbox from a network."""
        cmd = self._docker.build_network_cmd("disconnect", network, container)
        await self._docker.run_cmd(cmd)
        logger.info("sandbox_network_disconnected", container=container, network=network)
