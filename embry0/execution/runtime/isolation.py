"""Sandbox-to-sandbox isolation on ``sandbox-restricted`` (#554).

Every sandbox joins ``sandbox-restricted``, the bridge the credential
proxies listen on, and Docker leaves container-to-container traffic on for
that bridge. The egress rules (``egress.py``) key on a sandbox's
``sandbox-internet`` address, so without this module one job's sandbox can
open a connection to anything another job's sandbox listens on: a QA app's
dev server, a test database, a language server.

Enforcement model
-----------------
One chain for the bridge, not rules per sandbox. Every packet that enters
and leaves the ``sandbox-restricted`` bridge (container to container on it)
jumps from ``DOCKER-USER`` to ``EMBRY0-SANDBOX-ISOLATION``:

    [RETURN ESTABLISHED,RELATED] [RETURN -d proxy] ... [LOG] [DROP]

A new connection on the bridge passes only when it is addressed to a proxy;
the proxy's replies pass as ESTABLISHED. Nothing is keyed on a sandbox's
address, so there is nothing to remove when a sandbox ends, nothing a crash
can leak, and a sandbox that is given a former sandbox's address inherits no
rule. What can go stale is the proxy list: the proxies are recreated at every
orchestrator start, and Docker may then give a former proxy address to a new
sandbox. ``ensure`` rewrites the chain from the proxies running now, in one
``iptables-restore`` transaction, and ``SandboxManager`` calls it after the
proxies launch and before it hands any sandbox to a job.

The same transaction moves the jump to the top of ``DOCKER-USER``. The
per-sandbox egress rules are inserted there too, and their RETURN for a
declared private destination would otherwise skip this chain for a packet a
sandbox sends from its ``sandbox-internet`` address onto this bridge.

Out of scope here, and why that is safe:

- the bridge gateway is DinD itself: a sandbox's traffic to it is INPUT, not
  FORWARD, so a ``dind_enabled`` sandbox still reaches the daemon;
- ``sandbox-internet`` keeps the per-sandbox private-range drop (egress.py);
- ``qa-net-<job>`` belongs to one job, and Docker's own isolation chains drop
  traffic between bridges.

Fail-closed: ``ensure`` raises ``IsolationError`` unless, in DinD's network
namespace, bridged traffic reaches iptables at all
(``net.bridge.bridge-nf-call-iptables`` is 1; with it off no rule here sees
traffic between two containers on one bridge), the bridge interface exists,
``FORWARD`` jumps to ``DOCKER-USER`` in the iptables backend the helper
writes to, and the jump and the final DROP are in place after the write.
``SandboxManager.create`` destroys a sandbox when ``ensure`` raises.

"DinD" throughout means the daemon's network namespace. Under
``AGENT_RUNTIME=rootless`` that is rootlesskit's, where the bridges and the
helper's ``--network host`` both live (``rootless.py``), and every check above
applies there unchanged.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from typing import Any

import structlog

from embry0.execution.runtime.egress import EgressEnforcer
from embry0.safety.error_codes import ErrorCode

logger = structlog.get_logger(__name__)

RESTRICTED_NETWORK = "sandbox-restricted"
CHAIN = "EMBRY0-SANDBOX-ISOLATION"
LOG_PREFIX = "embry0-isolation-block: "

# Linux interface names: at most 15 characters. Docker names a bridge
# ``br-<first 12 of the network id>`` unless the network sets its own.
_IFNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,14}$")
_BRIDGE_NAME_OPTION = "com.docker.network.bridge.name"
# iptables chain names: at most 28 characters.
_CHAIN_NAME = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,27}$")

# Exit codes of the helper script's own guards, so a failure names its cause.
EXIT_BRIDGE_NETFILTER_OFF = 3
EXIT_BRIDGE_MISSING = 4
EXIT_NO_DOCKER_USER_HOOK = 5
EXIT_NOT_INSTALLED = 6


class IsolationError(RuntimeError):
    """The isolation chain could not be written or verified."""

    # A sandbox that cannot be isolated cannot start (classify_failure reads it).
    error_code = ErrorCode.SANDBOX_INIT


def bridge_interface(network: dict[str, Any]) -> str:
    """The Linux bridge behind a Docker bridge network, from ``docker network inspect``."""
    options = network.get("Options") or {}
    name = str(options.get(_BRIDGE_NAME_OPTION) or "")
    if not name:
        network_id = str(network.get("Id") or "")
        name = f"br-{network_id[:12]}" if len(network_id) >= 12 else ""
    if not _IFNAME.fullmatch(name):
        raise IsolationError(f"cannot tell the bridge interface of network {network.get('Name')!r} (got {name!r})")
    return name


def proxy_addresses(network: dict[str, Any], proxy_names: Iterable[str]) -> list[str]:
    """IPv4 addresses on ``network`` of the containers named in ``proxy_names``.

    Read from the network's own endpoint list, so a name that is not on the
    network (a proxy that stopped) contributes nothing, and no other
    container on it (a sandbox) can be mistaken for a proxy.
    """
    wanted = set(proxy_names)
    found: list[ipaddress.IPv4Address] = []
    for endpoint in (network.get("Containers") or {}).values():
        if endpoint.get("Name") not in wanted:
            continue
        raw = str(endpoint.get("IPv4Address") or "").split("/")[0]
        try:
            address = ipaddress.IPv4Address(raw)
        except ValueError as exc:
            raise IsolationError(f"proxy {endpoint.get('Name')!r} has no usable address ({raw!r})") from exc
        if address not in found:
            found.append(address)
    return [str(a) for a in sorted(found)]


def chain_rules(proxy_ips: Iterable[str], chain: str = CHAIN) -> list[str]:
    """The chain's rules, top-down, in ``iptables-restore`` syntax."""
    rules = [f"-A {chain} -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN"]
    for ip in proxy_ips:
        rules.append(f"-A {chain} -d {ipaddress.IPv4Address(ip)}/32 -j RETURN")
    rules.append(f'-A {chain} -m limit --limit 6/min -j LOG --log-prefix "{LOG_PREFIX}"')
    rules.append(f"-A {chain} -j DROP")
    return rules


def build_ensure_script(bridge: str, proxy_ips: Iterable[str], chain: str = CHAIN) -> str:
    """``sh`` script for the iptables helper: check, write in one transaction, verify.

    Every interpolated value is validated here, and the rules travel in a
    quoted heredoc, so the shell expands nothing in them.
    """
    if not _IFNAME.fullmatch(bridge):
        raise IsolationError(f"invalid bridge interface name {bridge!r}")
    if not _CHAIN_NAME.fullmatch(chain):
        raise IsolationError(f"invalid chain name {chain!r}")
    jump = f"DOCKER-USER -i {bridge} -o {bridge} -j {chain}"
    rules = "\n".join(chain_rules(proxy_ips, chain))
    return f"""set -eu
if [ "$(cat /proc/sys/net/bridge/bridge-nf-call-iptables 2>/dev/null)" != 1 ]; then
  echo "net.bridge.bridge-nf-call-iptables is not 1 in the DinD network namespace: iptables does not see traffic between containers on one bridge" >&2
  exit {EXIT_BRIDGE_NETFILTER_OFF}
fi
if ! ip link show dev {bridge} >/dev/null 2>&1; then
  echo "bridge {bridge} does not exist in the DinD network namespace" >&2
  exit {EXIT_BRIDGE_MISSING}
fi
if ! iptables -C FORWARD -j DOCKER-USER 2>/dev/null; then
  echo "FORWARD does not jump to DOCKER-USER in this iptables backend" >&2
  exit {EXIT_NO_DOCKER_USER_HOOK}
fi
MOVE='# jump not present yet'
if iptables -C {jump} 2>/dev/null; then MOVE='-D {jump}'; fi
{{
cat <<'RULES'
*filter
:{chain} - [0:0]
{rules}
RULES
echo "$MOVE"
cat <<'RULES'
-I DOCKER-USER 1 -i {bridge} -o {bridge} -j {chain}
COMMIT
RULES
}} | iptables-restore --noflush
# In place means evaluated: the jump comes before Docker's closing RETURN,
# and the chain ends in the DROP.
if ! iptables -S DOCKER-USER | awk -v jump='-A {jump}' '$0 == jump {{ ok = 1; exit }} $0 == "-A DOCKER-USER -j RETURN" {{ exit }} END {{ exit !ok }}' \\
  || [ "$(iptables -S {chain} | tail -n 1)" != "-A {chain} -j DROP" ]; then
  echo "isolation chain {chain} is not in place after the write" >&2
  exit {EXIT_NOT_INSTALLED}
fi
"""


class SandboxIsolation:
    """Writes and verifies the ``sandbox-restricted`` isolation chain on the DinD daemon."""

    def __init__(
        self,
        docker: Any,
        *,
        network: str = RESTRICTED_NETWORK,
        chain: str = CHAIN,
        helper: EgressEnforcer | None = None,
    ) -> None:
        self._docker = docker
        self._network = network
        self._chain = chain
        # The egress enforcer owns the iptables helper image and how it runs
        # in DinD's network namespace; this module reuses both.
        self._helper = helper if helper is not None else EgressEnforcer(docker)

    async def ensure(self, proxy_names: Iterable[str]) -> list[str]:
        """Rewrite the chain to allow the running ``proxy_names``. Returns their addresses.

        Refuses, without writing, when none of the proxies is on the
        network: a chain allowing no proxy would cut every running sandbox
        off from them, and keeping the last chain is the closed choice.
        """
        names = sorted(set(proxy_names))
        try:
            network = await self._docker.inspect_network(self._network)
        except Exception as exc:  # noqa: BLE001 — any inspect failure means no isolation
            raise IsolationError(f"cannot inspect network {self._network!r}: {exc}") from exc
        if network.get("EnableIPv6"):
            raise IsolationError(f"network {self._network!r} has IPv6 enabled; the isolation chain covers IPv4 only")
        bridge = bridge_interface(network)
        proxy_ips = proxy_addresses(network, names)
        if not proxy_ips:
            raise IsolationError(f"no proxy is running on {self._network!r} (expected some of {names})")
        on_network = {str(ep.get("Name")) for ep in (network.get("Containers") or {}).values()}
        missing = [name for name in names if name not in on_network]
        if missing:
            logger.warning("sandbox_isolation_proxy_absent", network=self._network, proxies=missing)
        script = build_ensure_script(bridge, proxy_ips, self._chain)
        try:
            await self._helper.ensure_helper_image()
            await self._helper._run_in_dind_netns(script)  # noqa: SLF001 — the helper's runner, shared with egress
        except Exception as exc:  # noqa: BLE001 — any failure here must fail closed
            raise IsolationError(f"isolation chain {self._chain} not installed on {bridge}: {exc}") from exc
        logger.info(
            "sandbox_isolation_ensured",
            network=self._network,
            bridge=bridge,
            chain=self._chain,
            proxies=proxy_ips,
        )
        return proxy_ips
