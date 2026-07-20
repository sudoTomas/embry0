"""Per-sandbox egress allowlist enforcement (EMB-29).

Egress control was binary: which DinD network a sandbox joins.
``sandbox-internet`` meant unrestricted NAT egress — an agent-driven
browser could roam the LAN of a host running a dozen prod services.

Enforcement model
-----------------
All sandbox egress is bridge-forwarded inside the **DinD daemon's**
network namespace, where Docker's ``DOCKER-USER`` filter chain applies
to every forwarded packet (and is never flushed by the daemon). Rules
are keyed on the sandbox's stable ``sandbox-internet`` source IP and
implement a **private-range blocklist with declared exceptions**:

    [allow LAN dst1] ... [allow ESTABLISHED] [LOG+DROP RFC1918/link-local]

Public-internet egress stays open (the clone to github.com, Auth0
login, CDN-fronted frontend URLs, Anthropic API all resolve to rotating
public IPs — L3-pinning them is DNS-fragile and was observed breaking
the clone on the first live run). The threat model this enforces is the
issue's: an agent-driven browser must NOT roam the host's LAN — every
RFC1918/CGN/link-local destination is dropped unless it is a declared
target (profile ``extra_hosts`` IPs / a private ``frontend_url``).
Cross-sandbox traffic on the egress bridge falls in the dropped ranges
too. Traffic on ``sandbox-restricted`` (git-proxy, minio-proxy, …) uses
a different source IP and is untouched.

The iptables binary must run **in DinD's netns** — the Docker API can't
program iptables — so every operation runs a short-lived helper
container on the DinD daemon with ``--network host --cap-add NET_ADMIN``
(host == the DinD container's namespace). The helper image is built
once on the DinD daemon from a two-line Dockerfile (alpine + iptables).

Blocked connections hit a rate-limited ``LOG`` rule with prefix
``embry0-egress-block:`` — visible via ``dmesg`` inside the dind
container — before the DROP.

Fail-closed: ``SandboxManager.create`` destroys the sandbox when rule
installation fails; a sandbox never runs with egress half-configured.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

HELPER_IMAGE = "embry0-egress-helper:latest"
_HELPER_DOCKERFILE = "FROM alpine:3.20\nRUN apk add --no-cache iptables\n"

LOG_PREFIX = "embry0-egress-block: "

# Private / non-routable ranges a sandbox must not reach unless declared.
# RFC1918 + carrier-grade NAT + link-local. Covers the host LAN
# (192.168.200.0/24 on corvin-server), every docker bridge, and the
# sandbox-internet bridge itself (blocking cross-sandbox traffic).
BLOCKED_PRIVATE_RANGES = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",
    "169.254.0.0/16",
)


def derive_deployed_egress_allowlist(
    *,
    frontend_url: str,
    extra_hosts: dict[str, str],
) -> list[str]:
    """Declared LAN targets for a deployed-target QA sandbox (EMB-29).

    Only PRIVATE destinations need declaring — public egress stays open
    (see module docstring). The declared set is:

    - every ``extra_hosts`` target IP (the profile's vhost→LAN-IP aliases —
      exactly the deployment's reachable surfaces),
    - the ``frontend_url`` host when it is (or resolves to) a private IP.
    """
    from urllib.parse import urlparse

    out: list[str] = []
    for ip in extra_hosts.values():
        if ip and ip not in out:
            out.append(ip)
    host = urlparse(frontend_url).hostname or ""
    if host:
        for ip in resolve_allowlist_to_ips([host]):
            if ipaddress.ip_address(ip.split("/")[0]).is_private and ip not in out:
                out.append(ip)
    return out


def resolve_allowlist_to_ips(entries: list[str]) -> list[str]:
    """Resolve hostnames/IPs/CIDRs to the concrete destinations iptables gets.

    IPv4 only (the sandbox bridges are v4). Hostnames resolve to ALL their
    A records at rule-install time — a snapshot by design; re-resolution
    happens per sandbox, which bounds staleness to a sandbox lifetime.
    Unresolvable entries are logged and skipped (the destination simply
    stays unreachable — fail closed, not open).
    """
    out: list[str] = []
    for entry in entries:
        target = entry.strip()
        if not target:
            continue
        # Bare IP or CIDR passes through.
        try:
            ipaddress.ip_network(target, strict=False)
            if ":" not in target and target not in out:
                out.append(target)
            continue
        except ValueError:
            pass
        try:
            infos = socket.getaddrinfo(target, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
        except OSError as exc:
            logger.warning("egress_allowlist_resolve_failed", entry=target, error=str(exc))
            continue
        for info in infos:
            ip = info[4][0]
            if ip not in out:
                out.append(ip)
    return out


class EgressEnforcer:
    """Installs/removes per-sandbox DOCKER-USER rules on the DinD daemon."""

    def __init__(self, docker: Any, helper_image: str = HELPER_IMAGE) -> None:
        self._docker = docker
        self._helper_image = helper_image

    async def _run_in_dind_netns(self, script: str, timeout: int = 30) -> str:
        """Run ``sh -c <script>`` in DinD's network namespace with NET_ADMIN."""
        cmd = self._docker._build_base_cmd()  # noqa: SLF001 — same access pattern as sandbox_manager
        cmd.extend(
            [
                "run",
                "--rm",
                "--network",
                "host",
                "--cap-add",
                "NET_ADMIN",
                "--entrypoint",
                "sh",
                self._helper_image,
                "-c",
                script,
            ]
        )
        return str(await self._docker.run_cmd(cmd, timeout=timeout))

    async def ensure_helper_image(self) -> None:
        """Build the iptables helper image on the DinD daemon if absent."""
        base = self._docker._build_base_cmd()  # noqa: SLF001
        try:
            await self._docker.run_cmd([*base, "image", "inspect", self._helper_image], timeout=15)
            return
        except Exception:  # noqa: BLE001 — absent image is the normal first-run case
            pass
        build_cmd = [
            "bash",
            "-c",
            (f"printf '{_HELPER_DOCKERFILE}' | " + " ".join(base) + f" build -t {self._helper_image} -"),
        ]
        await self._docker.run_cmd(build_cmd, timeout=120)
        logger.info("egress_helper_image_built", image=self._helper_image)

    async def verify_ready(self) -> bool:
        """Startup probe (mirrors assert_sandbox_networks_or_die's role).

        Non-fatal: enforcement itself fails closed per-sandbox, so an
        unavailable helper at boot only means early operator warning.
        """
        try:
            await self.ensure_helper_image()
            await self._run_in_dind_netns("iptables -S DOCKER-USER >/dev/null")
        except Exception as exc:  # noqa: BLE001
            logger.warning("egress_enforcement_unavailable", error=str(exc))
            return False
        logger.info("egress_enforcement_ready")
        return True

    async def container_ip(self, container_id: str, network: str = "sandbox-internet") -> str | None:
        base = self._docker._build_base_cmd()  # noqa: SLF001
        fmt = f'{{{{with index .NetworkSettings.Networks "{network}"}}}}{{{{.IPAddress}}}}{{{{end}}}}'
        out = str(await self._docker.run_cmd([*base, "inspect", "--format", fmt, container_id], timeout=15)).strip()
        return out or None

    async def apply(self, source_ip: str, allowed_destinations: list[str]) -> None:
        """Install the rule block for ``source_ip``.

        Private-range blocklist with declared exceptions (see module
        docstring). Insert order (each at position 1) produces top-down:
        LAN allows → ESTABLISHED/RELATED → per-range LOG → per-range DROP.
        Public destinations never match and fall through to Docker's own
        chains (normal NAT egress).
        """
        ipaddress.ip_address(source_ip)  # refuse garbage before it reaches a shell
        dests = resolve_allowlist_to_ips(allowed_destinations)
        lines: list[str] = []
        for blocked in BLOCKED_PRIVATE_RANGES:
            lines.append(f"iptables -I DOCKER-USER 1 -s {source_ip} -d {blocked} -j DROP")
        for blocked in BLOCKED_PRIVATE_RANGES:
            lines.append(
                f"iptables -I DOCKER-USER 1 -s {source_ip} -d {blocked} "
                f"-m limit --limit 6/min -j LOG --log-prefix '{LOG_PREFIX}'"
            )
        lines.append(f"iptables -I DOCKER-USER 1 -s {source_ip} -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN")
        for dst in dests:
            ipaddress.ip_network(dst, strict=False)
            lines.append(f"iptables -I DOCKER-USER 1 -s {source_ip} -d {dst} -j RETURN")
        await self._run_in_dind_netns(" && ".join(lines))
        logger.info(
            "sandbox_egress_rules_applied",
            source_ip=source_ip,
            allowed=allowed_destinations,
            resolved=dests,
            blocked_ranges=list(BLOCKED_PRIVATE_RANGES),
        )

    async def clear(self, source_ip: str) -> None:
        """Delete every DOCKER-USER rule keyed on ``source_ip``. Idempotent.

        Deletes by line number (re-listing after each delete) rather than by
        rule spec — the LOG rule's quoted ``--log-prefix`` does not survive
        shell word-splitting of ``iptables -S`` output.
        """
        ipaddress.ip_address(source_ip)
        script = (
            f"while n=$(iptables -L DOCKER-USER --line-numbers -n | "
            f"awk '$0 ~ /{source_ip}/ {{print $1; exit}}'); [ -n \"$n\" ]; do "
            "iptables -D DOCKER-USER $n; done"
        )
        await self._run_in_dind_netns(script)
        logger.info("sandbox_egress_rules_removed", source_ip=source_ip)
