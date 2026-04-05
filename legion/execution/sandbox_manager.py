"""Sandbox container lifecycle management via DinD.

Handles: create (persistent container), exec (agent runs), destroy (cleanup).
No customer code on the host — sandbox clones repo internally.
"""

from typing import Any

import structlog

from legion.execution.docker_client import DockerClient

logger = structlog.get_logger(__name__)

_DEFAULT_PROFILE: dict[str, Any] = {
    "base_image": "legion-sandbox:latest",
    "memory": "8g",
    "cpus": "4",
    "pids_limit": 256,
    "read_only_root": True,
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

        # Copy Claude credentials into sandbox (volume mounts don't work with DinD)
        await self._copy_credentials(name)

        return container_id

    async def _copy_credentials(self, container_name: str) -> None:
        """Copy Claude OAuth credentials into the sandbox container."""
        import os
        import subprocess
        import tempfile

        src = "/home/orchestrator/.claude"
        if not os.path.isdir(src):
            logger.warning("claude_credentials_not_found", path=src)
            return

        try:
            # Create a tar of the credentials and pipe into docker cp
            # docker cp requires tar format for directories
            with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tmp:
                tmp_path = tmp.name

            # Create tar with just the credentials files we need
            subprocess.run(
                ["tar", "cf", tmp_path, "-C", src, ".credentials.json", ".claude.json"],
                check=True,
                capture_output=True,
                timeout=10,
            )

            # Create the target dir in container via docker exec
            mkdir_cmd = self._docker.build_exec_cmd(
                container_name, ["bash", "-c", "mkdir -p /home/agent/.claude"]
            )
            await self._docker.run_cmd(mkdir_cmd, timeout=10)

            # Copy tar into container
            cp_cmd = self._docker._build_base_cmd()
            cp_cmd.extend(["cp", tmp_path, f"{container_name}:/tmp/claude-creds.tar"])
            await self._docker.run_cmd(cp_cmd, timeout=10)

            # Extract inside container
            extract_cmd = self._docker.build_exec_cmd(
                container_name,
                ["bash", "-c", "cd /home/agent/.claude && tar xf /tmp/claude-creds.tar && rm /tmp/claude-creds.tar"],
            )
            await self._docker.run_cmd(extract_cmd, timeout=10)

            os.unlink(tmp_path)
            logger.info("claude_credentials_copied", container=container_name)

        except Exception as exc:
            logger.warning("claude_credentials_copy_failed", container=container_name, error=str(exc))

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
