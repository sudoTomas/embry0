"""The container runtime seam (#268).

``AgentRuntime`` is the one object through which the orchestrator reaches the
daemon that runs sandboxes, driven by the Docker CLI: the DinD service
(``DockerRuntime``, the default) or a dedicated rootless Docker daemon
(``RootlessDockerRuntime``, ``AGENT_RUNTIME=rootless``). The protocol is shaped from what callers do
today and grows only with a caller: an operation is added in the change that
moves its first caller behind it.

``client`` is the Docker CLI wrapper the runtime drives. The ``run_cmd`` /
``build_exec_cmd`` call sites that predate the seam reach the daemon through
it; they are not behind the seam yet.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from embry0.execution.docker_client import DockerClient


@dataclass(frozen=True)
class ContainerSummary:
    """One container as ``list_containers`` reports it.

    ``created_at``, ``status`` and ``running_for`` are the daemon's own
    human-readable strings, unparsed.
    """

    id: str
    name: str
    created_at: str
    status: str
    running_for: str


@dataclass(frozen=True)
class VolumeSummary:
    """One named volume as ``inspect_volumes`` reports it.

    ``created_at`` is the daemon's own timestamp string, unparsed.
    """

    name: str
    labels: dict[str, str]
    created_at: str | None


@runtime_checkable
class AgentRuntime(Protocol):
    """The daemon that runs sandboxes, as the orchestrator uses it."""

    @property
    def kind(self) -> str:
        """Short name of the implementation, for logs (``"docker"``)."""
        ...

    @property
    def client(self) -> DockerClient:
        """The Docker CLI wrapper for call sites not yet behind the seam."""
        ...

    @property
    def sandbox_docker_access(self) -> bool:
        """Whether a sandbox may be handed this daemon (profile ``dind_enabled``, QA ``mode: dind``).

        Even then only with an admin's per-repo grant (#522). False means
        every such request is refused before anything is created.
        """
        ...

    def oci_runtime_refusal(self, runtime: str) -> str | None:
        """Why a sandbox profile may not run under OCI runtime ``runtime``, or None when it may."""
        ...

    async def verify_daemon(self) -> None:
        """Refuse to return unless the daemon is one this runtime may use.

        Called once at startup, before anything is built, run or pulled on
        the daemon; a failure means the orchestrator does not start.
        """
        ...

    async def verify_networks(self) -> None:
        """Refuse to return unless the sandbox networks exist and are restricted.

        Called once at startup, before any proxy launches; a failure means
        the orchestrator does not start.
        """
        ...

    # --- network policy ---------------------------------------------------

    async def verify_policy_ready(self) -> bool:
        """Startup probe: can sandbox network policy be enforced at all?

        Non-fatal by design: ``attach_policy`` and ``ensure_isolation`` fail
        closed per sandbox, so False only means an early operator warning.
        """
        ...

    async def attach_policy(
        self,
        container: str,
        *,
        allowed_destinations: Sequence[str],
        reachable_containers: Sequence[str],
    ) -> None:
        """Close the sandbox's internet-network egress to private addresses.

        Public destinations stay open. ``allowed_destinations`` (hostnames,
        IPs or CIDRs) are the declared private exceptions, and so are the
        containers named in ``reachable_containers`` (the proxies a sandbox
        must still reach). A named container whose address cannot be read is
        left out, which keeps it closed. Raises when the policy cannot be put
        in place; the caller must not hand that sandbox to a job.

        The proxy exceptions are shared by every sandbox, so this also
        rewrites them for every sandbox (#568).
        """
        ...

    async def refresh_egress(self, proxies: Sequence[str]) -> list[str]:
        """Point every sandbox's proxy exceptions at the running ``proxies`` (#568).

        The exceptions are one rule set shared by every sandbox, rewritten
        from the proxies' internet-network addresses now; a sandbox whose
        rules predate that rule set is converted to it. Returns the allowed
        proxy addresses; raises when the rules cannot be written and verified.
        """
        ...

    async def detach_policy(self, container: str) -> None:
        """Remove what ``attach_policy`` put in place for ``container``.

        A container that is gone or never had a policy is not an error;
        raises when rules that exist cannot be removed.
        """
        ...

    async def ensure_isolation(self, proxies: Sequence[str]) -> list[str]:
        """Let sandboxes on the restricted network reach ``proxies`` and no other container (#554).

        One rule set shared by every sandbox, rewritten from the proxies
        running now. Returns the allowed proxy addresses; raises
        ``IsolationError`` when it cannot be written and verified.
        """
        ...

    # --- containers -------------------------------------------------------

    async def list_containers(
        self,
        *,
        name: str | None = None,
        label: str | None = None,
        volume: str | None = None,
        include_stopped: bool = True,
        timeout: int = 15,
    ) -> list[ContainerSummary]:
        """Containers matching every filter given.

        ``name`` matches anywhere in the container name (Docker's name filter).
        ``label`` is ``key`` or ``key=value``. ``volume`` keeps the containers
        that mount that volume.
        """
        ...

    async def container_image(self, container: str, *, timeout: int = 10) -> tuple[str, str]:
        """``(image name, image ID)`` the container was started from, as the daemon reports them."""
        ...

    async def container_created(self, container: str, *, timeout: int = 10) -> str:
        """The container's creation time as the daemon reports it (RFC 3339)."""
        ...

    async def remove_containers(self, containers: Sequence[str], *, timeout: int = 15) -> None:
        """Force-remove the containers, running or not."""
        ...

    async def container_mounts(self, container: str, *, timeout: int = 10) -> list[tuple[str, str]]:
        """``(source, destination)`` of every mount of the container, as the daemon reports them."""
        ...

    async def mounted_sources(self, *, timeout: int = 30) -> set[str]:
        """The mount source of every mount of every container on the daemon, running or not."""
        ...

    async def chown_volume(self, volume: str, mount: str, *, image: str, owner: str, timeout: int = 30) -> None:
        """Hand the root of a named volume to ``owner``.

        Runs in a one-shot container holding CAP_CHOWN and nothing else, with
        no network, so no sandbox ever needs the capability (#453). ``image``
        must already be on the daemon.
        """
        ...

    # --- volumes ----------------------------------------------------------

    async def list_volumes(
        self, *, name: str | None = None, labels: Sequence[str] = (), timeout: int = 10
    ) -> list[str]:
        """Names of the volumes matching every filter given.

        ``name`` matches anywhere in the volume name (Docker's name filter);
        each of ``labels`` is ``key`` or ``key=value``.
        """
        ...

    async def inspect_volumes(self, volumes: Sequence[str], *, timeout: int = 30) -> list[VolumeSummary]:
        """Name, labels and creation time of each volume."""
        ...

    async def create_volume(self, volume: str, *, labels: Mapping[str, str], timeout: int = 10) -> None:
        """Create a named volume carrying ``labels``."""
        ...

    async def remove_volumes(self, volumes: Sequence[str], *, force: bool = False, timeout: int = 15) -> None:
        """Remove volumes. Without ``force`` the daemon refuses one a container still mounts."""
        ...

    # --- networks ---------------------------------------------------------

    async def create_network(self, network: str, *, labels: Mapping[str, str] | None = None, timeout: int = 15) -> None:
        """Create a bridge network."""
        ...

    async def connect_network(self, network: str, container: str, *, timeout: int = 10) -> None:
        """Attach a running container to ``network``."""
        ...

    async def list_networks(self, *, name: str, timeout: int = 10) -> list[str]:
        """IDs of the networks whose name matches ``name`` (Docker's name filter, a regex)."""
        ...

    async def remove_network(self, network: str, *, timeout: int = 10) -> None:
        """Remove a network. Fails while a container is attached to it."""
        ...

    # --- images -----------------------------------------------------------

    async def image_exists(self, image: str, *, timeout: int = 60) -> bool:
        """Whether the daemon holds ``image``.

        False when the daemon says no; a timeout still raises.
        """
        ...

    async def image_digests(self, image: str, *, timeout: int = 10) -> tuple[list[str], str]:
        """``(repository digests, local image ID)`` of ``image`` (a tag or an image ID).

        Repository digests are ``<repository>@sha256:...`` entries; a local-only
        image has none.
        """
        ...

    async def pull_image(self, image: str, *, timeout: int = 180) -> None:
        """Refresh the daemon's copy of a registry-qualified ``image``."""
        ...

    async def push_image(self, image: str, target: str) -> str:
        """Tag ``image`` as ``target`` and push it. Returns the push output, which carries the digest."""
        ...

    async def image_label(self, image: str, key: str, *, timeout: int = 10) -> str:
        """The value of label ``key`` on ``image``, empty when unset."""
        ...

    async def build_image(
        self,
        tag: str,
        *,
        dockerfile: str,
        context: str,
        labels: Mapping[str, str] | None = None,
        timeout: int = 300,
    ) -> None:
        """Build ``tag`` from ``dockerfile`` and the build context directory ``context``."""
        ...

    async def remove_image(self, image: str, *, timeout: int = 30) -> None:
        """Force-remove ``image`` from the daemon."""
        ...

    # --- QA compose workspaces (mode: dind) ---------------------------------

    async def compose_log_stream(self, project: str, service: str, *, tail: int = 200) -> asyncio.subprocess.Process:
        """Follow one compose service's logs; stdout carries them, stderr folded in.

        The caller owns the process: it reads it and terminates it.
        """
        ...

    async def compose_services(self, project: str, *, timeout: int = 10) -> list[str]:
        """Service names of a running compose project.

        Raises RuntimeError when the project cannot be queried.
        """
        ...
