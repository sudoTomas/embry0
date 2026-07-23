"""DinD-aware Docker CLI wrapper.

All Docker operations go through the CLI, respecting DOCKER_HOST and TLS
settings for Docker-in-Docker communication.
"""

import asyncio
import json
from typing import Any, cast

import structlog

logger = structlog.get_logger(__name__)


# Env-var keys whose values must never reach logs. The docker run command
# passes credentials/tokens via `-e KEY=VAL` argv pairs (sandbox launch
# threads CLAUDE_CODE_OAUTH_TOKEN, the per-sandbox EMBRY0_SANDBOX_TOKEN,
# etc.) and the same argv shows up at debug level on docker_cmd, at
# warning level on docker_cmd_timeout, and at error level on
# docker_cmd_failed. Scrub before logging.
# Non-secret env keys whose values are safe + useful to keep in logs. Every other
# `-e KEY=VAL` value is redacted (default-deny), so a newly-injected secret is
# scrubbed automatically. The previous allowlist leaked AZURE / CF / DB / CRON /
# HUB / REPORTS secrets because they were never added to it.
_SAFE_ENV_KEYS = frozenset(
    {
        "DOCKER_HOST",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    }
)


def _scrub_cmd_for_log(cmd: list[str]) -> list[str]:
    """Return a copy of ``cmd`` with secret ``-e KEY=VAL`` values redacted.

    Walks the argv looking for the docker `-e` flag followed by a
    ``KEY=VAL`` argument. If ``KEY`` is NOT in ``_SAFE_ENV_KEYS`` (default-deny),
    the value is replaced with ``***REDACTED***``. Any other argv token is preserved
    verbatim. Order and length are preserved so structured log consumers
    that index into the array see the same shape.
    """
    scrubbed: list[str] = []
    i = 0
    while i < len(cmd):
        token = cmd[i]
        if token == "-e" and i + 1 < len(cmd):
            kv = cmd[i + 1]
            key, sep, _ = kv.partition("=")
            if sep and key not in _SAFE_ENV_KEYS:
                scrubbed.append(token)
                scrubbed.append(f"{key}=***REDACTED***")
                i += 2
                continue
        scrubbed.append(token)
        i += 1
    return scrubbed


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

    @staticmethod
    def _registry_qualified(image: str) -> bool:
        """True when ``image`` is qualified against a registry host —
        first path segment contains ``.``/``:`` or is ``localhost``
        (registry:5000/..., ghcr.io/..., 1.2.3.4:5000/...). Only then is
        ``--pull=always`` valid; a bare local-only name would fail to pull.
        """
        first_segment = image.split("/", 1)[0] if "/" in image else ""
        return bool(first_segment) and ("." in first_segment or ":" in first_segment or first_segment == "localhost")

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
        pids_limit: int = 512,
        cap_drop: list[str] | None = None,
        cap_add: list[str] | None = None,
        security_opt: list[str] | None = None,
        read_only: bool = True,
        env: dict[str, str] | None = None,
        volumes: list[str] | None = None,
        tmpfs_mounts: list[str] | None = None,
        extra_hosts: dict[str, str] | None = None,
    ) -> list[str]:
        """Build `docker run -d` command for sandbox container.

        ``extra_hosts`` maps hostname → IP for ``--add-host=name:ip`` flags;
        used by SandboxManager to publish the backend IPs of host-side proxies
        (minio-proxy, presign-proxy) into a DinD-spawned sandbox's /etc/hosts
        so the sandbox can reach those proxies by name.

        ``tmpfs_mounts`` is a list of in-container paths to mount as
        ephemeral tmpfs (rw, nosuid). Each becomes a ``--tmpfs <path>:rw,nosuid``
        flag. Used by sub-task launches to overlay an empty tmpfs on top of
        a shared cache volume (e.g. ``/workspace/.qa``) so the per-sub-task
        directory isn't shared across containers — without it, all 14 fan-out
        sub-tasks would write the SAME ``/workspace/.qa/result.json`` and
        the last writer wins (silent test poisoning).
        """
        cmd = self._build_base_cmd()
        cmd.extend(["run", "-d", "--init"])
        # Registry-qualified image => force a pull on every launch. This keeps
        # DinD from serving stale `latest` after the host has rebuilt and
        # init-push-images has refreshed the registry — a previous silent
        # failure mode where the inner docker daemon's cached layers won an
        # implicit race against the registry's newer digest.
        if self._registry_qualified(image):
            cmd.append("--pull=always")
        cmd.extend(["--name", name])
        cmd.append(f"--network={network}")
        cmd.append(f"--memory={memory}")
        cmd.append(f"--cpus={cpus}")
        cmd.append(f"--pids-limit={pids_limit}")
        cmd.append(f"--memory-swap={memory}")  # equal to --memory => no swap
        cmd.append("--ulimit=nofile=4096:8192")

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

        for path in tmpfs_mounts or []:
            # mode=1777 makes the tmpfs root world-writable + sticky (same
            # default as /tmp). Without it Docker mounts the tmpfs at mode
            # 0755 owned by root, and a non-root sandbox user (the `agent`
            # account in the sandbox image) cannot create files in it —
            # `mkdir /workspace/.qa/logs` errors with `Permission denied`
            # and the QA fan-out aborts every sub-task with infra_failure.
            cmd.extend(["--tmpfs", f"{path}:rw,nosuid,mode=1777"])

        for host, ip in (extra_hosts or {}).items():
            cmd.append(f"--add-host={host}:{ip}")

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
    ) -> tuple[list[str], str]:
        """Build `docker run -d` for a proxy container, returning (cmd, env_file_path).

        Proxies hold the orchestrator's credentials. Pass them via --env-file
        (mode 0600) instead of -e KEY=VAL argv to keep them out of host
        `ps -ef` output. Caller is responsible for unlinking env_file_path
        after `docker run` returns (the container has snapshotted its env).
        """
        import os
        import tempfile

        fd, env_file_path = tempfile.mkstemp(prefix=f"embry0-proxy-env-{name}-", suffix=".env")
        try:
            with os.fdopen(fd, "w") as f:
                for key, value in env.items():
                    # No quoting: docker --env-file does not interpret quotes;
                    # value is taken literally up to newline.
                    f.write(f"{key}={value}\n")
            os.chmod(env_file_path, 0o600)
        except Exception:
            try:
                os.unlink(env_file_path)
            except OSError:
                pass
            raise

        cmd = self._build_base_cmd()
        cmd.extend(["run", "-d"])
        # Same stale-`latest` guard as build_run_cmd. Proxies missed this flag
        # and DinD silently ran a 2026-05-04 cached embry0-proxy for two
        # months — the pushed per-owner-token proxy never started until an
        # access smoke test exposed it (2026-07-06).
        if self._registry_qualified(image):
            cmd.append("--pull=always")
        cmd.extend(["--name", name])
        cmd.append(f"--network={network}")
        cmd.extend(["--env-file", env_file_path])
        cmd.append(image)
        return cmd, env_file_path

    def build_exec_cmd(
        self,
        container: str,
        command: list[str],
        workdir: str | None = None,
        env: dict[str, str] | None = None,
        interactive: bool = False,
    ) -> list[str]:
        """Build `docker exec` command. ``interactive`` adds ``-i`` (stdin attached)."""
        cmd = self._build_base_cmd()
        cmd.append("exec")
        if interactive:
            cmd.append("-i")
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

    async def inspect_network(self, name: str) -> dict[str, Any]:
        """Return parsed `docker network inspect <name>` output.

        Raises RuntimeError if the network does not exist or inspect fails.
        """
        cmd = self._build_base_cmd()
        cmd.extend(["network", "inspect", name])
        output = await self.run_cmd(cmd, timeout=15)
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"docker network inspect {name} returned non-JSON: {output[:200]}") from exc
        if not isinstance(data, list) or not data:
            raise RuntimeError(f"docker network inspect {name} returned empty list")
        return cast(dict[str, Any], data[0])

    async def assert_sandbox_networks_or_die(self) -> None:
        """Verify sandbox-restricted has masquerade disabled and sandbox-internet exists.

        Refuses to return on misconfiguration. Called from app startup before
        any proxy launches; failure means the orchestrator does not start.
        """
        try:
            restricted = await self.inspect_network("sandbox-restricted")
        except RuntimeError as exc:
            raise RuntimeError(
                "sandbox-restricted network missing — refusing to start. "
                "Run infra/scripts/setup-sandbox-networks.sh inside DinD; "
                "see docs/architecture.md § Docker Network Segmentation. "
                f"(inspect error: {exc})"
            ) from exc

        opts = restricted.get("Options") or {}
        masq = opts.get("com.docker.network.bridge.enable_ip_masquerade")
        if masq != "false":
            raise RuntimeError(
                "sandbox-restricted exists but enable_ip_masquerade is "
                f"{masq!r} (expected 'false'). Sandboxes would have direct "
                "internet egress. Delete the network and re-run "
                "infra/scripts/setup-sandbox-networks.sh."
            )

        try:
            await self.inspect_network("sandbox-internet")
        except RuntimeError as exc:
            raise RuntimeError(
                "sandbox-internet network missing — refusing to start. "
                "Run infra/scripts/setup-sandbox-networks.sh inside DinD."
            ) from exc

        logger.info("sandbox_networks_verified")

    async def run_cmd(self, cmd: list[str], timeout: int = 60) -> str:
        """Execute a Docker command and return stdout.

        Raises RuntimeError on non-zero exit. Raises TimeoutError on timeout
        (and kills the subprocess to prevent zombie Docker commands that would
        otherwise block the Docker daemon).
        """
        scrubbed = _scrub_cmd_for_log(cmd)
        logger.debug("docker_cmd", cmd=scrubbed)
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
            logger.warning("docker_cmd_timeout", cmd=scrubbed, timeout=timeout)
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                logger.error("docker_cmd_kill_stuck", cmd=scrubbed, pid=proc.pid)
            # A bare TimeoutError has an empty str() — every caller folding
            # {exc} into a failure_summary then reports "failed: " with no
            # cause (observed: "sandbox clone failed: " on job-06f63d7ba031).
            raise TimeoutError(f"docker command timed out after {timeout}s") from None

        if proc.returncode != 0:
            err = stderr.decode().strip()
            logger.error("docker_cmd_failed", cmd=scrubbed, stderr=err, returncode=proc.returncode)
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
        logger.debug("docker_stream_exec", cmd=_scrub_cmd_for_log(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return proc

    async def exec_with_stdin(
        self,
        container: str,
        command: list[str],
        stdin_bytes_path: str,
        timeout: int = 300,
    ) -> None:
        """Run `docker exec -i` feeding a host-side file to the command's stdin.

        Sibling of stream_exec for the write direction — used to stream tar
        archives into a sandbox (local workspace-init contexts) without a
        host bind mount. Raises RuntimeError on non-zero exit.
        """
        cmd = self.build_exec_cmd(container, command, interactive=True)
        logger.debug("docker_exec_with_stdin", cmd=_scrub_cmd_for_log(cmd), stdin=stdin_bytes_path)
        with open(stdin_bytes_path, "rb") as stdin_file:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=stdin_file,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except TimeoutError:
                proc.kill()
                raise RuntimeError(f"Docker exec-with-stdin timed out after {timeout}s")
        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            logger.error("docker_cmd_failed", cmd=_scrub_cmd_for_log(cmd), stderr=err, returncode=proc.returncode)
            raise RuntimeError(f"Docker command failed: {err}")

    async def commit_container(self, container_id: str, image_tag: str) -> str:
        """Commit a running container as a new image; returns the tag.

        Wraps ``docker commit <container_id> <image_tag>``. Used by the
        image builder (embry0/cache/image_builder.py) to bake a
        pre-installed sandbox into a reusable image layer.
        """
        cmd = self._build_base_cmd() + ["commit", container_id, image_tag]
        await self.run_cmd(cmd, timeout=120)
        return image_tag

    async def copy_into(self, container: str, src_path: str, dst_path: str) -> None:
        """Copy a host-side file into ``container`` at ``dst_path``.

        Thin wrapper around ``docker cp <src> <container>:<dst>``. Used by
        the orchestrator to inject session blobs / config files into a
        running sandbox before invoking the runner.

        Raises RuntimeError on docker exec failure.
        """
        cmd = self._build_base_cmd()
        cmd.extend(["cp", src_path, f"{container}:{dst_path}"])
        await self.run_cmd(cmd, timeout=30)

    async def copy_bytes_into(self, container: str, data: bytes, dst_path: str) -> None:
        """Copy in-memory ``data`` into ``container`` at ``dst_path``.

        Writes the bytes to a host-side temp file, ``docker cp``s it in,
        then unlinks the temp file. Convenience wrapper for callers that
        already hold the bytes in memory (e.g. session blobs read out of
        the AgentSessionsRepository).

        Raises RuntimeError on docker exec failure.
        """
        import os
        import tempfile

        fd, tmp_path = tempfile.mkstemp(prefix="embry0-copy-bytes-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            await self.copy_into(container, tmp_path, dst_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    async def copy_bytes_from(self, container: str, src_path: str) -> bytes:
        """Read ``src_path`` from inside ``container`` and return its bytes.

        Mirror of ``copy_bytes_into``: ``docker cp``s the in-container file
        to a host-side tempfile, reads it, and returns the bytes. Used by
        the AgentRunner to extract the Claude CLI session JSONL out of the
        sandbox after a claude_max-mode run, before the sandbox is destroyed.

        Raises RuntimeError on docker exec failure (e.g. file does not
        exist inside the container).
        """
        import os
        import tempfile

        fd, tmp_path = tempfile.mkstemp(prefix="embry0-copy-bytes-from-")
        os.close(fd)
        try:
            cmd = self._build_base_cmd()
            cmd.extend(["cp", f"{container}:{src_path}", tmp_path])
            await self.run_cmd(cmd, timeout=30)
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
