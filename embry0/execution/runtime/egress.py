"""Per-sandbox egress allowlist enforcement (EMB-29).

Egress control was binary: which DinD network a sandbox joins.
``sandbox-internet`` meant unrestricted NAT egress — an agent-driven
browser could roam the LAN of a host running a dozen prod services.

Enforcement model
-----------------
All sandbox egress is bridge-forwarded inside the **DinD daemon's**
network namespace, where Docker's ``DOCKER-USER`` filter chain applies
to every forwarded packet (and is never flushed by the daemon). It
implements a **private-range blocklist with declared exceptions**, in
two parts:

    DOCKER-USER, per sandbox, keyed on its ``sandbox-internet`` source IP:
      [RETURN -d declared dst1] ... [-j EMBRY0-SANDBOX-EGRESS]
    EMBRY0-SANDBOX-EGRESS, one chain shared by every sandbox:
      [RETURN ESTABLISHED] [RETURN -d proxy] ... [LOG+DROP RFC1918/CGN/link-local]

A RETURN in ``DOCKER-USER`` passes the packet on to Docker's own chains;
a RETURN in the shared chain comes back to ``DOCKER-USER``, where no
further rule of that sandbox drops it, so both are allows. A public
destination matches nothing and falls through.

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

The proxies that are ALSO on ``sandbox-internet`` are the one standing
exception (#472). A sandbox on both networks resolves their names to
the egress-bridge address, so without an allow for that address every
request to them is dropped. Those allows live in the shared chain, not
in any sandbox's rules (#568): the proxies are recreated at every
orchestrator start, and a sandbox that outlives the restart (paused,
awaiting input) would otherwise keep allows for the old addresses —
dropping its requests to a proxy that moved, and letting it reach the
next sandbox Docker gives an old proxy address to. ``refresh`` rewrites
the chain from the proxies running now, in one ``iptables-restore``
transaction, and converts the rules of a sandbox created before the
shared chain existed.

The iptables binary must run **in DinD's netns** — the Docker API can't
program iptables — so every operation runs a short-lived helper
container on the DinD daemon with ``--network host --cap-add NET_ADMIN``
(host == the DinD container's namespace). The helper image is built
once on the DinD daemon from a two-line Dockerfile (alpine + iptables).

Blocked connections are counted on the DROP rules:
``docker exec <dind> iptables -L EMBRY0-SANDBOX-EGRESS -v -n``. A rate-limited
``LOG`` rule with prefix ``embry0-egress-block:`` sits before each DROP,
but its line reaches the kernel log (``dmesg``) only when the host sets
``net.netfilter.nf_log_all_netns=1``: DinD has its own network
namespace, and the kernel drops ``LOG`` output from any namespace but
the initial one by default (#567).

Fail-closed: ``SandboxManager.create`` destroys the sandbox when rule
installation fails; a sandbox never runs with egress half-configured. A
jump to the shared chain is written only after the chain is verified to
end in its last DROP, since an empty chain would allow everything.

Under ``AGENT_RUNTIME=rootless`` "DinD" is the rootless daemon and its netns is
rootlesskit's: the same helper writes the same rules there (``rootless.py``).
Traffic that passes them leaves through slirp4netns, as the daemon's account.
"""

from __future__ import annotations

import ipaddress
import shlex
import socket
from collections.abc import Iterable
from typing import Any

import structlog

from embry0.execution.docker_client import DAEMON_LISTING_MAX_BYTES

logger = structlog.get_logger(__name__)

HELPER_IMAGE = "embry0-egress-helper:latest"
_HELPER_DOCKERFILE = "FROM alpine:3.20\nRUN apk add --no-cache iptables\n"

LOG_PREFIX = "embry0-egress-block: "

# Private / non-routable ranges a sandbox must not reach unless declared.
# RFC1918 + carrier-grade NAT + link-local. Covers the host LAN,
# every docker bridge, and the
# sandbox-internet bridge itself (blocking cross-sandbox traffic).
BLOCKED_PRIVATE_RANGES = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",
    "169.254.0.0/16",
)


EGRESS_CHAIN = "EMBRY0-SANDBOX-EGRESS"
# Exit code of the helper script when the chain is not in place after the write.
EXIT_CHAIN_NOT_INSTALLED = 6


def chain_rules(proxy_ips: Iterable[str], chain: str = EGRESS_CHAIN) -> list[str]:
    """The shared chain's rules, top-down, in ``iptables-restore`` syntax."""
    rules = [f"-A {chain} -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN"]
    for ip in proxy_ips:
        rules.append(f"-A {chain} -d {ipaddress.IPv4Address(ip)}/32 -j RETURN")
    for blocked in BLOCKED_PRIVATE_RANGES:
        rules.append(f'-A {chain} -d {blocked} -m limit --limit 6/min -j LOG --log-prefix "{LOG_PREFIX}"')
    for blocked in BLOCKED_PRIVATE_RANGES:
        rules.append(f"-A {chain} -d {blocked} -j DROP")
    return rules


def chain_write_script(proxy_ips: Iterable[str], chain: str = EGRESS_CHAIN) -> str:
    """``sh`` that rewrites the shared chain in one transaction, then checks it ends in its last DROP.

    Every interpolated value is validated by ``chain_rules``; the rules
    travel in a quoted heredoc, so the shell expands nothing in them.
    """
    rules = "\n".join(chain_rules(proxy_ips, chain))
    last = f"-A {chain} -d {BLOCKED_PRIVATE_RANGES[-1]} -j DROP"
    return f"""set -eu
iptables-restore --noflush <<'RULES'
*filter
:{chain} - [0:0]
{rules}
COMMIT
RULES
if [ "$(iptables -S {chain} | tail -n 1)" != '{last}' ]; then
  echo "egress chain {chain} is not in place after the write" >&2
  exit {EXIT_CHAIN_NOT_INSTALLED}
fi"""


def block_replace_script(source_ip: str, dests: Iterable[str], chain: str = EGRESS_CHAIN) -> str:
    """``sh`` that puts ``source_ip``'s block at the top of DOCKER-USER and drops its older rules.

    Top-down the block is the declared RETURNs, then the jump. Inserting
    first and deleting after means the sandbox is never without rules.
    Rules are matched on their ``-s`` field exactly, as ``clear`` does
    (#566); the first ``len(block)`` rules are the block just inserted.
    """
    source = ipaddress.ip_network(source_ip).with_prefixlen
    src = str(ipaddress.ip_address(source_ip))
    lines = [f"iptables -I DOCKER-USER 1 -s {src} -j {chain}"]
    declared = [str(ipaddress.ip_network(d, strict=False)) for d in dests]
    for dst in reversed(declared):
        lines.append(f"iptables -I DOCKER-USER 1 -s {src} -d {dst} -j RETURN")
    keep = len(declared) + 1
    lines.append(
        f"while n=$(iptables -S DOCKER-USER | awk -v src={source} -v keep={keep} "
        '\'$1 == "-A" { n++; if (n > keep) for (i = 2; i < NF; i++) if ($i == "-s" && $(i + 1) == src) '
        "{ print n; exit } }'); "
        '[ -n "$n" ]; do iptables -D DOCKER-USER $n; done'
    )
    return "\n".join(lines)


def legacy_blocks(listing: str, internet_subnets: Iterable[ipaddress.IPv4Network]) -> dict[str, list[str]]:
    """Sources whose DOCKER-USER rules predate the shared chain, with their declared targets.

    ``listing`` is ``iptables -S DOCKER-USER``. A pre-chain block carries
    its own ``LOG`` rule with ``LOG_PREFIX`` and no jump to the chain; any
    other source is left alone. Its RETURN destinations outside
    ``internet_subnets`` are its declared targets.
    """
    subnets = list(internet_subnets)
    logs: set[str] = set()
    jumps: set[str] = set()
    returns: dict[str, list[str]] = {}
    for line in listing.splitlines():
        try:
            words = shlex.split(line)
        except ValueError:
            continue
        if words[:2] != ["-A", "DOCKER-USER"] or "-s" not in words:
            continue
        src = words[words.index("-s") + 1]
        target = words[words.index("-j") + 1] if "-j" in words else ""
        if target == "LOG" and LOG_PREFIX.strip() in " ".join(words):
            logs.add(src)
        elif target == EGRESS_CHAIN:
            jumps.add(src)
        elif target == "RETURN" and "-d" in words and "--ctstate" not in words:
            returns.setdefault(src, []).append(words[words.index("-d") + 1])
    out: dict[str, list[str]] = {}
    for src in sorted(logs - jumps):
        address = src.split("/")[0]
        out[address] = [
            d for d in returns.get(src, []) if not any(ipaddress.IPv4Network(d).subnet_of(n) for n in subnets)
        ]
    return out


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
            # sockaddr is typed tuple[str, int]; for AF_INET element 0 is always
            # the address string, but the union needs narrowing for mypy.
            ip = info[4][0]
            if isinstance(ip, str) and ip not in out:
                out.append(ip)
    return out


class EgressEnforcer:
    """Installs/removes per-sandbox DOCKER-USER rules on the DinD daemon."""

    def __init__(self, docker: Any, helper_image: str = HELPER_IMAGE) -> None:
        self._docker = docker
        self._helper_image = helper_image

    async def _run_in_dind_netns(self, script: str, timeout: int = 30) -> str:
        """Run ``sh -c <script>`` in DinD's network namespace with NET_ADMIN."""
        cmd = self._docker._build_base_cmd()  # noqa: SLF001 — the runtime owns its client
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
        # `iptables -S DOCKER-USER` lists a few rules for every sandbox on the daemon.
        return str(await self._docker.run_cmd(cmd, timeout=timeout, max_bytes=DAEMON_LISTING_MAX_BYTES))

    async def ensure_helper_image(self) -> None:
        """Build the iptables helper image on the DinD daemon if absent."""
        base = self._docker._build_base_cmd()  # noqa: SLF001
        try:
            await self._docker.run_cmd(
                [*base, "image", "inspect", self._helper_image], timeout=15, expected_failure=True
            )
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

    async def apply(
        self,
        source_ip: str,
        allowed_destinations: list[str],
        proxy_ips: Iterable[str] = (),
    ) -> None:
        """Install the rule block for ``source_ip``, and the shared chain it jumps to.

        ``allowed_destinations`` (hostnames, IPs, CIDRs) are the sandbox's
        declared exceptions; ``proxy_ips`` the ``sandbox-internet``
        addresses of the proxies running now, which the shared chain allows
        for every sandbox. One helper run writes the chain, checks it, puts
        the sandbox's block at the top of ``DOCKER-USER`` and removes any
        older rule keyed on ``source_ip`` below it, so applying twice
        replaces rather than stacks.
        """
        ipaddress.ip_address(source_ip)  # refuse garbage before it reaches a shell
        dests = resolve_allowlist_to_ips(allowed_destinations)
        script = "\n".join([chain_write_script(proxy_ips), block_replace_script(source_ip, dests)])
        await self._run_in_dind_netns(script)
        logger.info(
            "sandbox_egress_rules_applied",
            source_ip=source_ip,
            allowed=allowed_destinations,
            resolved=dests,
            blocked_ranges=list(BLOCKED_PRIVATE_RANGES),
        )

    async def refresh(self, proxy_ips: Iterable[str], internet_subnets: Iterable[str]) -> list[str]:
        """Rewrite the shared chain for ``proxy_ips``; convert pre-chain sandbox rules (#568).

        A sandbox created before the shared chain existed holds its own
        private-range drop with RETURNs for the proxy addresses of its day.
        Its block is replaced by the current one: its RETURNs to addresses
        outside ``internet_subnets`` are its declared targets and are kept;
        those inside are proxy allows, now the chain's. Returns the source
        addresses converted.
        """
        proxies = [str(ipaddress.IPv4Address(ip)) for ip in proxy_ips]
        subnets = [ipaddress.IPv4Network(n, strict=False) for n in internet_subnets]
        listing = await self._run_in_dind_netns("iptables -S DOCKER-USER")
        legacy = legacy_blocks(listing, subnets)
        parts = [chain_write_script(proxies)]
        parts += [block_replace_script(src, dests) for src, dests in legacy.items()]
        await self._run_in_dind_netns("\n".join(parts))
        if legacy:
            logger.info("sandbox_egress_rules_converted", sources=sorted(legacy))
        logger.info("sandbox_egress_chain_refreshed", chain=EGRESS_CHAIN, proxies=proxies)
        return sorted(legacy)

    async def clear(self, source_ip: str) -> None:
        """Delete every DOCKER-USER rule keyed on ``source_ip``. Idempotent.

        Deletes by rule number (re-listing after each delete) rather than by
        rule spec — the LOG rule's quoted ``--log-prefix`` does not survive
        shell word-splitting of ``iptables -S`` output. A rule is matched by
        comparing its ``-s`` field to ``source_ip/32`` exactly: a substring
        match would also take the rules of ``x.y.z.80`` when clearing
        ``x.y.z.8``, and any rule whose destination is ``source_ip`` (#566).
        """
        source = ipaddress.ip_network(source_ip).with_prefixlen  # validates; "a.b.c.d/32", as -S prints it
        script = (
            f"while n=$(iptables -S DOCKER-USER | awk -v src={source} "
            '\'$1 == "-A" { n++; for (i = 2; i < NF; i++) if ($i == "-s" && $(i + 1) == src) { print n; exit } }\'); '
            '[ -n "$n" ]; do iptables -D DOCKER-USER $n; done'
        )
        await self._run_in_dind_netns(script)
        logger.info("sandbox_egress_rules_removed", source_ip=source_ip)
