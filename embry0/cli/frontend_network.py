"""The dashboard network's range, checked before ``embry0 start`` builds (#366).

The compose ``frontend`` network is pinned: nginx and the dashboard tunnel have
fixed addresses in it, and the orchestrator trusts X-Forwarded-For from exactly
those two (``FORWARDED_ALLOW_IPS``, RAV-1060, RAV-1069). ``FRONTEND_NETWORK_PREFIX``,
the first two octets of its /16, moves the range and both addresses together.
Docker refuses to create the network over part of another network's range, and
compose finds that out at ``up``, after every image is built; this check reads
the range from compose's own rendering and answers before the build.

It refuses a range outside the private ranges too. The egress chain keeps
sandboxes off those (``embry0/execution/runtime/egress.py``), and a public range
would hide the real addresses in it from the host. The isolation chain is not
involved: it lives in the sandbox daemon's network namespace and reads its
addresses from that daemon's networks.

``check`` is pure. ``inspect`` runs the three Docker commands it needs, through
the ``run`` it is given, and never prints the rendering it reads: compose renders
the orchestrator's ``env_file`` into it.
"""

from __future__ import annotations

import ipaddress
import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

SETTING = "FRONTEND_NETWORK_PREFIX"
NETWORK = "frontend"
PRIVATE_RANGES = tuple(ipaddress.ip_network(r) for r in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
_PRIVATE_TEXT = "10.0.0.0/8, 172.16.0.0/12 or 192.168.0.0/16"
_PROJECT_LABEL = "com.docker.compose.project"

Run = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class Finding:
    """What the check found: problems refuse the start; notes are printed and the start goes on."""

    problems: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def _subnets(config: Mapping[str, Any]) -> list[str]:
    network = (config.get("networks") or {}).get(NETWORK) or {}
    return [str(c["subnet"]) for c in ((network.get("ipam") or {}).get("config") or []) if c.get("subnet")]


def _foreign_subnets(networks: Sequence[Mapping[str, Any]], project: str) -> list[tuple[str, Any]]:
    """(name, network) for each subnet Docker reports on a network that is not this compose project's."""
    found: list[tuple[str, Any]] = []
    for net in networks:
        if (net.get("Labels") or {}).get(_PROJECT_LABEL) == project:
            continue
        for cfg in (net.get("IPAM") or {}).get("Config") or []:
            try:
                found.append((str(net.get("Name") or net.get("Id") or "?"), ipaddress.ip_network(cfg["Subnet"], False)))
            except (KeyError, TypeError, ValueError):
                continue
    return found


def _inside(net: Any, ranges: Sequence[Any]) -> bool:
    """Whether *net* lies inside one of *ranges* of its own IP version."""
    return any(net.version == r.version and net.subnet_of(r) for r in ranges)


def _forwarded_notes(config: Mapping[str, Any], ranges: Sequence[Any]) -> list[str]:
    env = ((config.get("services") or {}).get("orchestrator") or {}).get("environment") or {}
    raw = env.get("FORWARDED_ALLOW_IPS") if isinstance(env, Mapping) else None
    notes = []
    for entry in (e.strip() for e in str(raw or "").split(",")):
        if not entry or entry == "*":
            continue
        try:
            trusted = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        if not _inside(trusted, ranges):
            where = ", ".join(map(str, ranges))
            notes.append(
                f"FORWARDED_ALLOW_IPS trusts {entry}, which is not on the {NETWORK} network ({where}): the "
                f"orchestrator then takes nginx's and the tunnel's address for every client's. Unset, it follows "
                f"{SETTING}."
            )
    return notes


def check(config: Mapping[str, Any], networks: Sequence[Mapping[str, Any]], *, project: str, env_file: str) -> Finding:
    """The verdict on the rendered compose *config*, given the *networks* Docker reports (``docker network inspect``)."""
    subnets = _subnets(config)
    if not subnets:
        return Finding(
            notes=(f"the compose config names no subnet for the {NETWORK} network: its range is not checked",)
        )
    problems: list[str] = []
    ranges = []
    move = f"set {SETTING} in {env_file} to the first two octets of a free private /16, for example 10.229"
    for raw in subnets:
        try:
            wanted = ipaddress.ip_network(raw)
        except ValueError:
            problems.append(f"{SETTING} gives the {NETWORK} network {raw}, which is not a network: {move}")
            continue
        ranges.append(wanted)
        if not _inside(wanted, PRIVATE_RANGES):
            problems.append(
                f"the {NETWORK} network {wanted} is outside the private ranges ({_PRIVATE_TEXT}), which sandboxes "
                f"are kept from: {move}"
            )
            continue
        for name, other in _foreign_subnets(networks, project):
            if other.version == wanted.version and other.overlaps(wanted):
                problems.append(
                    f"the {NETWORK} network {wanted} overlaps {other} (network {name}), so Docker cannot create it: "
                    f"remove that network if nothing uses it, or {move}"
                )
    notes = _forwarded_notes(config, ranges) if ranges and not problems else []
    return Finding(problems=tuple(problems), notes=tuple(notes))


def inspect(compose_cmd: Sequence[str], *, project: str, env_file: str, run: Run | None = None) -> Finding:
    """Render the compose config *compose_cmd* names and check it against Docker's networks.

    A rendering or a network listing that cannot be read is a note, not a problem: the build and ``up`` that come
    next report their own error, and a check that could not run says so instead of passing.
    """
    run = run or subprocess.run
    rendered = run([*compose_cmd, "config", "--format", "json"], capture_output=True, text=True, check=False)
    try:
        config = json.loads(rendered.stdout) if rendered.returncode == 0 else None
    except ValueError:
        config = None
    if not isinstance(config, dict):
        return Finding(notes=(f"could not render the compose config: the {NETWORK} network's range is not checked",))
    listed = run(["docker", "network", "ls", "-q"], capture_output=True, text=True, check=False)
    ids = listed.stdout.split() if listed.returncode == 0 else None
    networks: Any = []
    if ids:
        # Inspect prints the networks it found even when one went away after the listing, and exits 1.
        inspected = run(["docker", "network", "inspect", *ids], capture_output=True, text=True, check=False)
        try:
            networks = json.loads(inspected.stdout or "[]")
        except ValueError:
            ids = None
    if ids is None or not isinstance(networks, list):
        return Finding(notes=(f"could not list Docker's networks: the {NETWORK} network's range is not checked",))
    return check(config, networks, project=project, env_file=env_file)
