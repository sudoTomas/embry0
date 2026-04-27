"""DinD-aware Docker CLI wrapper.

All Docker operations go through the CLI, respecting DOCKER_HOST and TLS
settings for Docker-in-Docker communication.
"""

import asyncio

import structlog

logger = structlog.get_logger(__name__)


class DockerClient:
    """Wraps Docker CLI commands for DinD environments."""

    def __init__(
        self,
        docker_host: str = "",
        tls_verify: bool = False,
        cert_path: str = "",
    ) -> None:
        self._host = docker_host
        self._tls_verify = tls_verify
        self._cert_path = cert_path

    def _build_base_cmd(self) -> list[str]:
        """Build base docker command with host and TLS flags."""
        cmd = ["docker"]
        if self._host:
            cmd.extend(["--host", self._host])
        if self._tls_verify and self._cert_path:
            cmd.append("--tlsverify")
            cmd.append(f"--tlscacert={self._cert_path}/ca.pem")
            cmd.append(f"--tlscert={self._cert_path}/cert.pem")
            cmd.append(f"--tlskey={self._cert_path}/key.pem")
        return cmd

    def build_run_cmd(
        self,
        image: str,
        name: str,
        network: str = "sandbox-restricted",
        memory: str = "8g",
        cpus: str = "4",
        pids_limit: int = 256,
        cap_drop: list[str] | None = None,
        cap_add: list[str] | None = None,
        security_opt: list[str] | None = None,
        read_only: bool = True,
        env: dict[str, str] | None = None,
        volumes: list[str] | None = None,
    ) -> list[str]:
        """Build `docker run -d` command for sandbox container."""
        cmd = self._build_base_cmd()
        cmd.extend(["run", "-d", "--init"])
        cmd.extend(["--name", name])
        cmd.append(f"--network={network}")
        cmd.append(f"--memory={memory}")
        cmd.append(f"--cpus={cpus}")
        cmd.append(f"--pids-limit={pids_limit}")

        for cap in cap_drop or ["ALL"]:
            cmd.append(f"--cap-drop={cap}")
        for cap in cap_add or []:
            cmd.append(f"--cap-add={cap}")
        for opt in security_opt or ["no-new-privileges"]:
            cmd.append(f"--security-opt={opt}")

        if read_only:
            cmd.append("--read-only")
            cmd.extend(["--tmpfs", "/tmp:rw,nosuid"])
            cmd.extend(["--tmpfs", "/home/agent/.claude:rw,nosuid"])

        for key, value in (env or {}).items():
            cmd.extend(["-e", f"{key}={value}"])

        for vol in volumes or []:
            cmd.extend(["-v", vol])

        cmd.extend([image, "sleep", "infinity"])
        return cmd

    def build_run_proxy_cmd(
        self,
        *,
        name: str,
        image: str,
        network: str,
        env: dict[str, str],
    ) -> list[str]:
        """Build `docker run -d` for a proxy container.

        Proxies hold the orchestrator's credentials and are trusted (unlike
        sandboxes). No cap-drop, no read-only fs — just a bare `docker run` on
        the requested network with the env vars.
        """
        cmd = self._build_base_cmd()
        cmd.extend(["run", "-d"])
        cmd.extend(["--name", name])
        cmd.append(f"--network={network}")
        for key, value in env.items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.append(image)
        return cmd

    def build_exec_cmd(
        self,
        container: str,
        command: list[str],
        workdir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> list[str]:
        """Build `docker exec` command."""
        cmd = self._build_base_cmd()
        cmd.append("exec")
        if workdir:
            cmd.extend(["-w", workdir])
        for key, value in (env or {}).items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.append(container)
        cmd.extend(command)
        return cmd

    def build_stop_cmd(self, container: str, timeout: int = 10) -> list[str]:
        """Build `docker stop` command."""
        cmd = self._build_base_cmd()
        cmd.extend(["stop", "-t", str(timeout), container])
        return cmd

    def build_rm_cmd(self, container: str) -> list[str]:
        """Build `docker rm` command."""
        cmd = self._build_base_cmd()
        cmd.extend(["rm", "-f", container])
        return cmd

    def build_network_cmd(self, action: str, network: str, container: str) -> list[str]:
        """Build `docker network connect/disconnect` command."""
        cmd = self._build_base_cmd()
        cmd.extend(["network", action, network, container])
        return cmd

    async def run_cmd(self, cmd: list[str], timeout: int = 60) -> str:
        """Execute a Docker command and return stdout.

        Raises RuntimeError on non-zero exit. Raises TimeoutError on timeout
        (and kills the subprocess to prevent zombie Docker commands that would
        otherwise block the Docker daemon).
        """
        logger.debug("docker_cmd", cmd=cmd)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            # The subprocess would otherwise keep running and hold a Docker slot.
            # Kill → wait briefly for exit → re-raise so callers can fail cleanly.
            logger.warning("docker_cmd_timeout", cmd=cmd, timeout=timeout)
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                logger.error("docker_cmd_kill_stuck", cmd=cmd, pid=proc.pid)
            raise

        if proc.returncode != 0:
            err = stderr.decode().strip()
            logger.error("docker_cmd_failed", cmd=cmd, stderr=err, returncode=proc.returncode)
            raise RuntimeError(f"Docker command failed: {err}")

        return stdout.decode().strip()

    async def stream_exec(
        self,
        container: str,
        command: list[str],
        workdir: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 300,
    ) -> asyncio.subprocess.Process:
        """Start a `docker exec` and return the process for streaming stdout."""
        cmd = self.build_exec_cmd(container, command, workdir=workdir, env=env)
        logger.debug("docker_stream_exec", cmd=cmd)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return proc
