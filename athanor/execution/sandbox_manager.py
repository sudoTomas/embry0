"""Sandbox container lifecycle management via DinD.

Handles: create (persistent container), exec (agent runs), destroy (cleanup).
No customer code on the host — sandbox clones repo internally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from athanor.execution.docker_client import DockerClient

if TYPE_CHECKING:
    from athanor.execution.proxy.manager import ProxyManager

logger = structlog.get_logger(__name__)


class SandboxInitError(RuntimeError):
    """Sandbox container creation or enrollment failed.

    Carries an ErrorCode so the workflow handler can populate jobs.error_code
    consistently.
    """

    def __init__(self, message: str, *, error_code: object) -> None:
        super().__init__(message)
        self.error_code = error_code


_DEFAULT_PROFILE: dict[str, Any] = {
    "base_image": "athanor-sandbox:latest",
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

    def __init__(self, docker: DockerClient, *, proxy_manager: ProxyManager) -> None:
        self._docker = docker
        self._proxy_manager = proxy_manager

    async def create(
        self,
        job_id: str,
        profile: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        """Create a persistent sandbox container. Returns (container_id, sandbox_token)."""
        p = {**_DEFAULT_PROFILE, **(profile or {})}
        name = f"sandbox-{job_id}"

        # Inject credentials into sandbox env
        env = dict(env) if env else {}
        oauth_token = self._read_oauth_token()
        if oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token

        # DinD: when the profile opts in, mount the dind-certs-client volume
        # RO at /certs/client and set DOCKER_HOST/TLS env vars so the Docker
        # CLI inside the sandbox can talk to tcp://dind:2376. The sandbox
        # must also be on a network that can reach the DinD daemon — that's
        # what `extra_networks` (typically ["backend"]) is for, and we
        # connect those networks after container create below.
        dind_enabled = bool(p.get("dind_enabled", False))
        extra_volumes: list[str] = []
        if dind_enabled:
            extra_volumes.append("dind-certs-client:/certs/client:ro")
            env.setdefault("DOCKER_HOST", "tcp://dind:2376")
            env.setdefault("DOCKER_TLS_VERIFY", "1")
            env.setdefault("DOCKER_CERT_PATH", "/certs/client")

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
            volumes=extra_volumes or None,
        )
        container_id = await self._docker.run_cmd(cmd)
        try:
            sandbox_token = await self._proxy_manager.enroll_sandbox(container_id)
        except RuntimeError as exc:
            logger.error("sandbox_enroll_failed_rolling_back", container=name, error=str(exc))
            # Clean any partial enrollment from earlier proxies before rm.
            # unenroll_sandbox is best-effort and never raises.
            await self._proxy_manager.unenroll_sandbox(container_id)
            try:
                await self._docker.run_cmd(self._docker.build_rm_cmd(name))
            except RuntimeError:
                logger.warning("sandbox_rollback_rm_failed", container=name)
            from athanor.safety.error_codes import ErrorCode

            raise SandboxInitError(
                f"Sandbox enrollment failed: {exc}",
                error_code=ErrorCode.SANDBOX_INIT,
            ) from exc

        # Attach extra networks (typically ["backend"] for DinD daemon access).
        # Done after enrollment so a network failure rolls back the same way as
        # any other create-path failure: we destroy the partially-set-up sandbox.
        extra_networks = list(p.get("extra_networks", []) or [])
        for net in extra_networks:
            try:
                await self.connect_network(container_id, net)
            except RuntimeError as exc:
                logger.error(
                    "sandbox_extra_network_failed_rolling_back",
                    container=name,
                    network=net,
                    error=str(exc),
                )
                await self._proxy_manager.unenroll_sandbox(container_id)
                try:
                    await self._docker.run_cmd(self._docker.build_rm_cmd(name))
                except RuntimeError:
                    logger.warning("sandbox_rollback_rm_failed", container=name)
                from athanor.safety.error_codes import ErrorCode

                raise SandboxInitError(
                    f"Sandbox extra-network attach failed ({net}): {exc}",
                    error_code=ErrorCode.SANDBOX_INIT,
                ) from exc

        logger.info(
            "sandbox_created",
            job_id=job_id,
            container=name,
            image=p.get("base_image"),
            dind_enabled=dind_enabled,
            extra_networks=extra_networks,
        )
        return container_id, sandbox_token

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
            raw_token = creds.get("claudeAiOauth", {}).get("accessToken")
            token: str | None = str(raw_token) if raw_token else None
            if token:
                logger.info("claude_oauth_token_loaded")
            return token
        except Exception as exc:
            logger.warning("claude_credentials_read_failed", error=str(exc))
            return None

    async def destroy(self, container: str, timeout: int = 10) -> None:
        """Stop and remove a sandbox container."""
        # Unenroll first so the proxy stops accepting the bearer; if this races
        # with `docker rm -f` below the worst case is a stale entry that the next
        # enrollment overwrites (sandbox_id == container_id; docker IDs are unique).
        try:
            await self._proxy_manager.unenroll_sandbox(container)
        except Exception:
            logger.warning("sandbox_unenroll_threw", container=container, exc_info=True)

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
