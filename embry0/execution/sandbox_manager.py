"""Sandbox container lifecycle management via DinD.

Handles: create (persistent container), exec (agent runs), destroy (cleanup).
No customer code on the host — sandbox clones repo internally.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import TYPE_CHECKING, Any

import structlog

from embry0.execution.auth_provider import RESERVED_ENV_KEYS, RESERVED_ENV_PREFIXES
from embry0.execution.docker_client import DockerClient
from embry0.execution.image_registry import qualify_image
from embry0.execution.worker_env import compute_worker_env
from embry0.safety.error_codes import ErrorCode

if TYPE_CHECKING:
    from embry0.execution.proxy.manager import ProxyManager

logger = structlog.get_logger(__name__)


class SandboxInitError(RuntimeError):
    """Sandbox container creation or enrollment failed.

    Carries an ErrorCode so the workflow handler can populate jobs.error_code
    consistently.
    """

    def __init__(self, message: str, *, error_code: object) -> None:
        super().__init__(message)
        self.error_code = error_code


# RFC-1123-ish hostname for profile extra_hosts keys. Kept local rather than
# imported from api.schemas — the execution layer must not depend on the API
# layer (same rule mirrored in SandboxProfileRequest._validate_extra_hosts).
_HOSTNAME_PATTERN = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,62}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,62}[A-Za-z0-9])?)*$")


def _sanitize_docker_name_component(s: str) -> str:
    """Replace characters invalid in Docker container/network names with '-'.

    Docker requires container/network names to match `[a-zA-Z0-9][a-zA-Z0-9_.-]+`.
    Colons and slashes (legal in embry0 job_ids) are replaced with hyphens.
    Mirrors VolumeManager._sanitize() in embry0/cache/volume_manager.py.
    """
    return s.replace(":", "-").replace("/", "-")


_DEFAULT_PROFILE: dict[str, Any] = {
    "base_image": "embry0-sandbox:latest",
    "memory": "8g",
    "cpus": "4",
    "pids_limit": 256,
    "read_only_root": False,  # Claude CLI needs writable rootfs for Node.js cache
    "cap_drop": ["ALL"],
    "cap_add": [],
    "security_opt": ["no-new-privileges"],
    "default_network": "sandbox-restricted",
    # sandbox-internet attached so the bundled Claude CLI (claude_max OAuth
    # mode) can reach api.anthropic.com — sandbox-restricted has
    # enable_ip_masquerade=false and blocks all egress, which makes the SDK's
    # initial Claude call hang forever (manifested as "agent timed out" with
    # cost=$0 and no tool_call events). Defense-in-depth still holds: cap_drop
    # ALL, no-new-privileges, Ring-3 PreToolUse hook, no static credentials in
    # env (only the per-job OAuth token + git credential proxy bearer).
    "extra_networks": ["sandbox-internet"],
    "extra_hosts": {},
    "container_timeout_seconds": 3600,
}


class SandboxManager:
    """Manages sandbox container lifecycle for job execution."""

    def __init__(
        self,
        docker: DockerClient,
        *,
        proxy_manager: ProxyManager,
        image_registry: str = "",
        github_token: str = "",
    ) -> None:
        self._docker = docker
        self._proxy_manager = proxy_manager
        self._image_registry = image_registry
        self._github_token = github_token

    def _resolve_token_for_repo(self, repo: str | None) -> str:
        from embry0.execution.github_tokens import resolve_for_repo

        return resolve_for_repo(repo, self._github_token)

    async def create(
        self,
        job_id: str,
        profile: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
        volumes: list[tuple[str, str]] | None = None,
        tmpfs_mounts: list[str] | None = None,
        repo: str | None = None,
    ) -> tuple[str, str]:
        """Create a persistent sandbox container. Returns (container_id, sandbox_token).

        ``volumes`` is an optional list of ``(volume_name, mount_point)`` pairs.
        Each pair is translated to a ``--mount type=volume,source=<vol>,target=<mp>``
        flag on the underlying ``docker run`` command.  Pass ``None`` (the default)
        to skip volume mounts entirely, preserving backward-compatible behaviour.

        Phase-2 usage: the shared-volume cache layer passes the pre-warmed
        ``node_modules`` volume here so it is available at ``/workspace`` when
        the sandbox starts.

        ``tmpfs_mounts`` overlays an in-memory tmpfs at each listed in-container
        path. Used by the multi-app QA fan-out: when the same shared volume is
        mounted into 14 sibling sub-task sandboxes, ``/workspace/.qa/`` would
        otherwise be the SAME physical directory across all of them and the
        last sub-task to write ``result.json`` would clobber every other
        sub-task's result. Mounting ``/workspace/.qa`` as a per-container
        tmpfs isolates the per-sub-task ephemeral state (job.json, result.json,
        screenshots) without sacrificing the shared read-cache for ``node_modules``
        and the cloned repo tree.
        """
        p = {**_DEFAULT_PROFILE, **(profile or {})}
        name = f"sandbox-{_sanitize_docker_name_component(job_id)}"

        # Inject credentials into sandbox env
        env = dict(env) if env else {}
        oauth_token = self._read_oauth_token()
        if oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token

        # Merge profile-level env_defaults (stored in DB but previously
        # never wired into the env passed to create).  Use setdefault so
        # caller-provided values win — env_defaults are profile baselines.
        # Reserved keys/prefixes are dropped: profiles must not become an
        # unfiltered env-injection path (same defense-in-depth the user
        # env-var path gets in _filter_user_env_for_sandbox).
        for key, value in (p.get("env_defaults") or {}).items():
            if key in RESERVED_ENV_KEYS or key.startswith(RESERVED_ENV_PREFIXES):
                logger.warning("profile_env_default_reserved_key_dropped", key=key)
                continue
            env.setdefault(key, value)

        # Inject worker-cap env vars derived from the container CPU limit.
        # Node.js os.cpus() returns the HOST cpu count (not the container
        # cgroup quota), so build tools that size worker pools from
        # os.cpus().length (Next.js, webpack, jest, …) spawn far more
        # workers than the sandbox's pids/memory limits allow — resulting
        # in EAGAIN on fork/pthread_create.  Cap them to the profile's
        # --cpus value.  setdefault lets user-provided overrides win.
        for key, value in compute_worker_env(p["cpus"]).items():
            env.setdefault(key, value)

        # DinD: when the profile opts in, bind-mount /certs/client RO into the
        # sandbox and set DOCKER_HOST/TLS env vars so the Docker CLI inside the
        # sandbox can talk to tcp://dind:2376. The sandbox must also be on a
        # network that can reach the DinD daemon — that's what `extra_networks`
        # (typically ["backend"]) is for, and we connect those networks after
        # container create below.
        #
        # NOTE: this is a BIND-MOUNT (leading "/"), not a named volume. We are
        # asking DinD's daemon to spawn a container, and DinD interprets volume
        # sources in ITS OWN namespace. A name like "dind-certs-client" would
        # be interpreted as a DinD-internal volume (which doesn't exist) and
        # silently created empty — the sandbox would then fail TLS handshake.
        # The host's `dind-certs-client` named volume IS mounted into the DinD
        # container at /certs/client by compose, so bind-mounting that path
        # surfaces the real certs to the sandbox.
        dind_enabled = bool(p.get("dind_enabled", False))
        extra_volumes: list[str] = []
        # Profile-level host aliases (EMB-28): emitted as --add-host flags so a
        # sandbox on sandbox-internet can reach an externally deployed app by
        # its real vhost name (DinD NAT is by-IP only; Docker DNS cannot
        # resolve host/LAN names from nested sandboxes). Validated here as
        # defense-in-depth — profiles can enter the DB without passing the API
        # schema (seeds, direct SQL). The 'dind' alias is orchestrator-owned
        # and assigned after this merge, so a profile can never override it.
        extra_hosts: dict[str, str] = {}
        for host, ip in (p.get("extra_hosts") or {}).items():
            if host == "dind" or not _HOSTNAME_PATTERN.match(host):
                logger.warning("profile_extra_host_invalid_name_dropped", host=host)
                continue
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                logger.warning("profile_extra_host_invalid_ip_dropped", host=host, ip=ip)
                continue
            extra_hosts[host] = ip
        if dind_enabled:
            extra_volumes.append("/certs/client:/certs/client:ro")
            env.setdefault("DOCKER_HOST", "tcp://dind:2376")
            env.setdefault("DOCKER_TLS_VERIFY", "1")
            env.setdefault("DOCKER_CERT_PATH", "/certs/client")
            # Sandboxes on sandbox-restricted reach the dind daemon by IP via
            # the bridge gateway (which IS the dind container). Without an
            # /etc/hosts entry the hostname `dind` doesn't resolve from inside
            # DinD-spawned containers; resolution would otherwise require an
            # extra_networks attachment to a network that doesn't exist
            # (dind's own backend membership is host-side only). Phase 1.5.
            extra_hosts["dind"] = self._resolve_dind_gateway_ip()

        # Phase-2 named-volume mounts (shared-volume cache layer C2/C3).
        # Each (volume_name, mount_point) pair is appended as a
        # "volume_name:mount_point" string so build_run_cmd emits -v flags.
        # This is standard Docker syntax for named volumes (no leading "/" on
        # the source side, which distinguishes them from bind-mounts).
        for vol_name, mount_point in volumes or []:
            extra_volumes.append(f"{vol_name}:{mount_point}")

        base_image = p.get("base_image", _DEFAULT_PROFILE["base_image"])
        cmd = self._docker.build_run_cmd(
            image=qualify_image(base_image, self._image_registry),
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
            tmpfs_mounts=tmpfs_mounts or None,
            extra_hosts=extra_hosts or None,
        )
        container_id = await self._docker.run_cmd(cmd)
        resolved_token = self._resolve_token_for_repo(repo)
        try:
            sandbox_token = await self._proxy_manager.enroll_sandbox(container_id, github_token=resolved_token)
        except RuntimeError as exc:
            logger.error("sandbox_enroll_failed_rolling_back", container=name, error=str(exc))
            # Clean any partial enrollment from earlier proxies before rm.
            # unenroll_sandbox is best-effort and never raises.
            await self._proxy_manager.unenroll_sandbox(container_id)
            try:
                await self._docker.run_cmd(self._docker.build_rm_cmd(name))
            except RuntimeError:
                logger.warning("sandbox_rollback_rm_failed", container=name)
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
            named_volumes=[(v, m) for v, m in (volumes or [])],
        )
        return container_id, sandbox_token

    @staticmethod
    def _resolve_dind_gateway_ip() -> str:
        """Resolve the dind container's IP on the host backend network.

        DinD-spawned sandboxes reach the dind daemon at
        ``tcp://dind:2376``, but the hostname ``dind`` only exists in the
        HOST docker DNS (compose backend network), not inside DinD's own DNS.
        The orchestrator IS on backend, so it can resolve the name; we then
        inject the IP into the sandbox via ``--add-host=dind:<ip>``.

        From inside DinD-spawned containers on sandbox-restricted, traffic to
        this IP routes through the sandbox-restricted bridge gateway (which
        is the dind container itself, with eth0 on backend) — so the
        connection succeeds without NAT (dind's eth0 IS the destination
        interface from the dind kernel's perspective).
        """
        try:
            return socket.gethostbyname("dind")
        except OSError as exc:
            raise RuntimeError(
                "Cannot resolve `dind` from the orchestrator. The orchestrator "
                "must be on the compose `backend` network (where the dind "
                "service lives) for dind_enabled sandboxes to function."
            ) from exc

    @staticmethod
    def _read_oauth_token() -> str | None:
        """Resolve the Claude OAuth token used by sandbox-side claude CLI.

        Resolution order (first non-empty wins):
          1. The CLAUDE_CODE_OAUTH_TOKEN env var on the orchestrator. This
             is the documented direct override and works when the host's
             ~/.claude credentials file isn't readable from inside the
             container (e.g. UID-mismatched bind mount).
          2. /home/orchestrator/.claude/.credentials.json — read the
             ``claudeAiOauth.accessToken`` field. This is the path the
             host's claude CLI persists to.

        Returns None if neither source produces a token. Callers (the QA
        agent config layer) raise ERR_MISSING_OAUTH_TOKEN on None.
        """
        import json
        import os

        env_token = (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
        if env_token:
            logger.info("claude_oauth_token_loaded", source="env")
            return env_token

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
                logger.info("claude_oauth_token_loaded", source="credentials_file")
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
