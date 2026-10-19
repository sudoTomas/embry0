"""``AgentRuntime`` over the Docker CLI (today: the DinD daemon)."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping, Sequence

import structlog

from embry0.execution.docker_client import DockerClient
from embry0.execution.runtime import egress as _egress
from embry0.execution.runtime import isolation as _isolation
from embry0.execution.runtime.protocol import ContainerSummary, DiskUsage, ImageSummary, VolumeSummary

logger = structlog.get_logger(__name__)

# The network sandboxes get NAT egress on; the egress rules key on their address there.
_INTERNET_NETWORK = "sandbox-internet"

# Tab-separated: no field of `docker ps` output contains a tab.
_PS_FORMAT = "{{.ID}}\t{{.Names}}\t{{.CreatedAt}}\t{{.Status}}\t{{.RunningFor}}"

# `range`, not `join`: on the DinD daemon .RepoDigests decodes as
# []interface{} and `join` fails there with a template error
# (verified against the live DinD, 2026-09-20).
_DIGEST_FORMAT = "{{range .RepoDigests}}{{.}},{{end}}|{{.Id}}"

_INSPECT_CHUNK = 50

# `docker system df` prints sizes in decimal units (go-units HumanSize).
_SIZE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([kKMGTP]?B)\s*$")
_SIZE_UNITS = {"B": 1, "kB": 10**3, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12, "PB": 10**15}


def parse_size(text: str) -> int | None:
    """Bytes in a size the daemon printed (``400.7MB``), or None when it is not one."""
    match = _SIZE.match(text or "")
    if match is None:
        return None
    return int(float(match.group(1)) * _SIZE_UNITS[match.group(2)])


class DockerRuntime:
    """Drives a Docker daemon through ``DockerClient``.

    Which daemon is the client's configuration (``DOCKER_HOST`` and TLS), so
    a different daemon is a different client, not a different runtime.
    """

    kind = "docker"
    # The DinD daemon is nested in its own privileged container, so an admin
    # may grant a repo's sandboxes its client certificates (#522).
    sandbox_docker_access = True

    def __init__(self, client: DockerClient) -> None:
        self._client = client

    @property
    def client(self) -> DockerClient:
        return self._client

    def _cmd(self, *args: str) -> list[str]:
        return [*self._client._build_base_cmd(), *args]  # noqa: SLF001 — the runtime owns its client

    def oci_runtime_refusal(self, runtime: str) -> str | None:
        # No profile field selects an OCI runtime on DinD; nothing to refuse.
        return None

    async def verify_daemon(self) -> None:
        # The DinD daemon is reached over TLS with the certs compose made for
        # it; nothing about it to check that the network check does not.
        return None

    async def verify_networks(self) -> None:
        await self._client.assert_sandbox_networks_or_die()

    # --- network policy ---------------------------------------------------
    # iptables in the daemon's network namespace, written by a helper
    # container (egress.py, isolation.py). The classes are looked up on their
    # modules at call time.

    async def verify_policy_ready(self) -> bool:
        return await _egress.EgressEnforcer(self._client).verify_ready()

    async def attach_policy(
        self,
        container: str,
        *,
        allowed_destinations: Sequence[str],
        reachable_containers: Sequence[str],
    ) -> None:
        enforcer = _egress.EgressEnforcer(self._client)
        source_ip = await enforcer.container_ip(container, _INTERNET_NETWORK)
        if not source_ip:
            raise RuntimeError(f"no {_INTERNET_NETWORK} IP on container")
        await enforcer.ensure_helper_image()
        reachable = await self._internet_addresses(enforcer, reachable_containers)
        await enforcer.apply(source_ip, list(allowed_destinations), proxy_ips=reachable)

    async def refresh_egress(self, proxies: Sequence[str]) -> list[str]:
        # The proxies' addresses and the bridge's subnets come from one
        # network inspect; a proxy that is not on the network contributes
        # nothing, so its old address is allowed no longer.
        network = await self._client.inspect_network(_INTERNET_NETWORK)
        addresses = _isolation.proxy_addresses(network, proxies)
        subnets = [
            str(cfg["Subnet"])
            for cfg in ((network.get("IPAM") or {}).get("Config") or [])
            if ":" not in str(cfg.get("Subnet") or ":")
        ]
        if not subnets:
            raise RuntimeError(f"network {_INTERNET_NETWORK!r} reports no IPv4 subnet")
        enforcer = _egress.EgressEnforcer(self._client)
        await enforcer.ensure_helper_image()
        await enforcer.refresh(addresses, subnets)
        return addresses

    async def _internet_addresses(self, enforcer: _egress.EgressEnforcer, containers: Sequence[str]) -> list[str]:
        """``sandbox-internet`` addresses of ``containers``, skipping (with a warning) any it cannot read.

        A proxy that needs internet egress sits on both sandbox networks, and
        a sandbox on both resolves its name to the ``sandbox-internet`` side
        — inside the private range the egress rules drop (#472). A proxy
        whose address cannot be read is left out: that keeps the sandbox's
        egress closed, and a proxy that is gone cannot be reached either way.
        """
        addresses: list[str] = []
        for name in containers:
            try:
                address = await enforcer.container_ip(name, _INTERNET_NETWORK)
            except Exception as exc:  # noqa: BLE001 — an unreadable proxy must not open or fail the sandbox
                logger.warning("sandbox_egress_proxy_address_unreadable", proxy=name, error=str(exc))
                continue
            if not address:
                logger.warning("sandbox_egress_proxy_address_unreadable", proxy=name, error="no address")
                continue
            if address not in addresses:
                addresses.append(address)
        return addresses

    async def detach_policy(self, container: str) -> None:
        # Before the container is removed, while its address still resolves:
        # the rules live in the daemon's DOCKER-USER chain, so removing the
        # container alone would orphan them (and the address could later be
        # given to another sandbox).
        enforcer = _egress.EgressEnforcer(self._client)
        try:
            source_ip = await enforcer.container_ip(container, _INTERNET_NETWORK)
        except Exception:  # noqa: BLE001 — already-removed / never-networked containers
            return
        if source_ip:
            await enforcer.clear(source_ip)

    async def ensure_isolation(self, proxies: Sequence[str]) -> list[str]:
        return await _isolation.SandboxIsolation(self._client).ensure(proxies)

    # --- containers -------------------------------------------------------

    async def list_containers(
        self,
        *,
        name: str | None = None,
        label: str | None = None,
        volume: str | None = None,
        ancestor: str | None = None,
        include_stopped: bool = True,
        timeout: int = 15,
    ) -> list[ContainerSummary]:
        args = ["ps"]
        if include_stopped:
            args.append("-a")
        if name is not None:
            args += ["--filter", f"name={name}"]
        if label is not None:
            args += ["--filter", f"label={label}"]
        if volume is not None:
            args += ["--filter", f"volume={volume}"]
        if ancestor is not None:
            args += ["--filter", f"ancestor={ancestor}"]
        args += ["--format", _PS_FORMAT]
        output = await self._client.run_cmd(self._cmd(*args), timeout=timeout)
        found: list[ContainerSummary] = []
        for line in (output or "").split("\n"):
            fields = line.split("\t")
            if len(fields) < 5 or not fields[0].strip():
                continue
            found.append(ContainerSummary(*(f.strip() for f in fields[:5])))
        return found

    async def container_created(self, container: str, *, timeout: int = 10) -> str:
        output = await self._client.run_cmd(
            self._cmd("inspect", container, "--format", "{{.Created}}"), timeout=timeout
        )
        return str(output).strip()

    async def container_image(self, container: str, *, timeout: int = 10) -> tuple[str, str]:
        output = await self._client.run_cmd(
            self._cmd("inspect", "--format", "{{.Config.Image}}|{{.Image}}", container), timeout=timeout
        )
        name, _, image_id = (output or "").strip().partition("|")
        return name, image_id

    async def remove_containers(self, containers: Sequence[str], *, timeout: int = 15) -> None:
        await self._client.run_cmd(self._cmd("rm", "-f", *containers), timeout=timeout)

    def chown_volume_cmd(self, volume: str, mount: str, *, image: str, owner: str) -> list[str]:
        """The `docker run` behind ``chown_volume``.

        Not a `docker exec` in the sandbox (#453): the sandbox drops every
        capability, so root inside it cannot chown. Only the mount point is
        chowned: a populated volume must not be walked on every start.
        """
        return self._cmd(
            "run",
            "--rm",
            "--network=none",
            "--cap-drop=ALL",
            "--cap-add=CHOWN",
            "--security-opt=no-new-privileges",
            "--user",
            "root",
            "-v",
            f"{volume}:{mount}",
            "--entrypoint",
            "chown",
            image,
            owner,
            mount,
        )

    async def chown_volume(self, volume: str, mount: str, *, image: str, owner: str, timeout: int = 30) -> None:
        await self._client.run_cmd(self.chown_volume_cmd(volume, mount, image=image, owner=owner), timeout=timeout)

    # --- volumes ----------------------------------------------------------

    async def list_volumes(
        self, *, name: str | None = None, labels: Sequence[str] = (), timeout: int = 10
    ) -> list[str]:
        args = ["volume", "ls", "--quiet"]
        if name is not None:
            args += ["--filter", f"name={name}"]
        for label in labels:
            args += ["--filter", f"label={label}"]
        output = await self._client.run_cmd(self._cmd(*args), timeout=timeout)
        return (output or "").split()

    async def inspect_volumes(self, volumes: Sequence[str], *, timeout: int = 30) -> list[VolumeSummary]:
        raw = await self._client.run_cmd(self._cmd("volume", "inspect", *volumes), timeout=timeout)
        return [
            VolumeSummary(
                name=entry["Name"],
                labels=dict(entry.get("Labels") or {}),
                created_at=entry.get("CreatedAt"),
            )
            for entry in json.loads(raw or "[]")
        ]

    async def create_volume(self, volume: str, *, labels: Mapping[str, str], timeout: int = 10) -> None:
        label_args: list[str] = []
        for key, value in labels.items():
            label_args += ["--label", f"{key}={value}"]
        await self._client.run_cmd(self._cmd("volume", "create", *label_args, volume), timeout=timeout)

    async def remove_volumes(self, volumes: Sequence[str], *, force: bool = False, timeout: int = 15) -> None:
        await self._client.run_cmd(
            self._cmd("volume", "rm", *(["--force"] if force else []), *volumes), timeout=timeout
        )

    # --- networks ---------------------------------------------------------

    async def create_network(self, network: str, *, labels: Mapping[str, str] | None = None, timeout: int = 15) -> None:
        label_args: list[str] = []
        for key, value in (labels or {}).items():
            label_args += ["--label", f"{key}={value}"]
        await self._client.run_cmd(self._cmd("network", "create", *label_args, network), timeout=timeout)

    async def connect_network(self, network: str, container: str, *, timeout: int = 10) -> None:
        await self._client.run_cmd(self._cmd("network", "connect", network, container), timeout=timeout)

    async def list_networks(self, *, name: str, timeout: int = 10) -> list[str]:
        output = await self._client.run_cmd(
            self._cmd("network", "ls", "-q", "--filter", f"name={name}"), timeout=timeout
        )
        return (output or "").split()

    async def remove_network(self, network: str, *, timeout: int = 10) -> None:
        await self._client.run_cmd(self._cmd("network", "rm", network), timeout=timeout)

    # --- images -----------------------------------------------------------

    async def image_exists(self, image: str, *, timeout: int = 60) -> bool:
        try:
            await self._client.run_cmd(self._cmd("image", "inspect", image), timeout=timeout, expected_failure=True)
        except RuntimeError:
            return False
        return True

    async def image_digests(self, image: str, *, timeout: int = 10) -> tuple[list[str], str]:
        output = await self._client.run_cmd(
            self._cmd("image", "inspect", "--format", _DIGEST_FORMAT, image), timeout=timeout
        )
        repo_digests, _, local_id = (output or "").strip().rpartition("|")
        return [entry.strip() for entry in repo_digests.split(",") if entry.strip()], local_id

    async def pull_image(self, image: str, *, timeout: int = 180) -> None:
        await self._client.run_cmd(self._cmd("pull", "--quiet", image), timeout=timeout)

    async def push_image(self, image: str, target: str) -> str:
        await self._client.run_cmd(self._cmd("tag", image, target), timeout=60)
        return str(await self._client.run_cmd(self._cmd("push", target), timeout=600))

    async def image_label(self, image: str, key: str, *, timeout: int = 10) -> str:
        fmt = f'{{{{index .Config.Labels "{key}"}}}}'
        output = await self._client.run_cmd(self._cmd("inspect", "--format", fmt, image), timeout=timeout)
        return str(output).strip()

    async def build_image(
        self,
        tag: str,
        *,
        dockerfile: str,
        context: str,
        labels: Mapping[str, str] | None = None,
        timeout: int = 300,
    ) -> None:
        args = ["build", "-t", tag]
        for key, value in (labels or {}).items():
            args += ["--label", f"{key}={value}"]
        args += ["-f", dockerfile, context]
        await self._client.run_cmd(self._cmd(*args), timeout=timeout)

    async def remove_image(self, image: str, *, timeout: int = 30) -> None:
        await self._client.run_cmd(self._cmd("rmi", "-f", image), timeout=timeout)

    async def list_images(self, *, timeout: int = 60) -> list[ImageSummary]:
        listed = await self._client.run_cmd(self._cmd("image", "ls", "--quiet", "--no-trunc"), timeout=timeout)
        ids = list(dict.fromkeys((listed or "").split()))  # one line per tag: keep each image once
        images: list[ImageSummary] = []
        for i in range(0, len(ids), _INSPECT_CHUNK):
            raw = await self._client.run_cmd(
                self._cmd("image", "inspect", *ids[i : i + _INSPECT_CHUNK]), timeout=timeout
            )
            for entry in json.loads(raw or "[]"):
                images.append(
                    ImageSummary(
                        id=str(entry.get("Id") or ""),
                        repo_tags=tuple(str(t) for t in entry.get("RepoTags") or ()),
                        repo_digests=tuple(str(d) for d in entry.get("RepoDigests") or ()),
                        created_at=str(entry.get("Created") or ""),
                        size=int(entry.get("Size") or 0),
                    )
                )
        return images

    async def remove_image_refs(self, refs: Sequence[str], *, timeout: int = 60) -> None:
        # No -f: the daemon refuses the last reference of an image a container uses.
        await self._client.run_cmd(self._cmd("rmi", *refs), timeout=timeout)

    async def disk_usage(self, *, timeout: int = 120) -> DiskUsage:
        raw = await self._client.run_cmd(self._cmd("system", "df", "-v", "--format", "{{json .}}"), timeout=timeout)
        data = json.loads(raw or "{}")
        images: dict[str, int] = {}
        for entry in data.get("Images") or ():
            size = parse_size(str(entry.get("UniqueSize") or ""))
            if size is not None and entry.get("ID"):
                images[str(entry["ID"])] = size
        volumes: dict[str, int] = {}
        for entry in data.get("Volumes") or ():
            size = parse_size(str(entry.get("Size") or ""))
            if size is not None and entry.get("Name"):
                volumes[str(entry["Name"])] = size
        return DiskUsage(image_unique_bytes=images, volume_bytes=volumes)

    # --- QA compose workspaces (mode: dind) ---------------------------------

    async def compose_log_stream(self, project: str, service: str, *, tail: int = 200) -> asyncio.subprocess.Process:
        # `--` keeps ``service`` from ever being read as a flag.
        cmd = self._cmd("compose", "-p", project, "logs", "-f", "--tail", str(tail), "--", service)
        return await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

    async def compose_services(self, project: str, *, timeout: int = 10) -> list[str]:
        proc = await asyncio.create_subprocess_exec(
            *self._cmd("compose", "-p", project, "ps", "--services"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            raise
        if proc.returncode != 0:
            raise RuntimeError(f"docker compose ps for {project} exited {proc.returncode}")
        return [line.strip() for line in out.decode(errors="replace").splitlines() if line.strip()]
