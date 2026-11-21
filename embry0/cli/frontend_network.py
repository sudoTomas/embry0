"""The dashboard network's range, checked before the stack is built (#366).

The compose ``frontend`` network is pinned: nginx and the dashboard tunnel have
fixed addresses in it, and the orchestrator trusts X-Forwarded-For from exactly
those two (``FORWARDED_ALLOW_IPS``, RAV-1060, RAV-1069). ``FRONTEND_NETWORK_PREFIX``,
the first two octets of its /16, moves the range and both addresses together.
Docker refuses to create the network over part of another network's range, and
compose finds that out at ``up``, after every image is built; this check reads
the range from compose's own rendering and answers before the build.

Compose reads the setting from one of two env files. ``embry0 start`` passes the
root ``.env``; ``infra/deploy.sh`` and the systemd unit run compose from
``infra/`` and read ``infra/.env``. Set in one only, the paths put the stack on
different ranges, and the orchestrator trusts addresses nobody has. So both
``embry0 start`` and ``infra/deploy.sh`` refuse when the two files disagree, and
when the range they render is not the one the stack's network already has.

It refuses a range outside the private ranges too. The egress chain keeps
sandboxes off those (``embry0/execution/runtime/egress.py``), and a public range
would hide the real addresses in it from the host. The isolation chain is not
involved: it lives in the sandbox daemon's network namespace and reads its
addresses from that daemon's networks.

``check`` is pure. ``inspect`` runs the three Docker commands it needs, through
the ``run`` it is given, and never prints the rendering it reads: compose renders
the orchestrator's ``env_file`` into it. This file imports only the standard
library: ``infra/deploy.sh`` runs it as a script (``main``), with no environment.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SETTING = "FRONTEND_NETWORK_PREFIX"
NETWORK = "frontend"
PRIVATE_RANGES = tuple(ipaddress.ip_network(r) for r in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
_PRIVATE_TEXT = "10.0.0.0/8, 172.16.0.0/12 or 192.168.0.0/16"
_PROJECT_LABEL = "com.docker.compose.project"
_NETWORK_LABEL = "com.docker.compose.network"
#: The env files compose interpolates, by who reads them: ``embry0 start`` the first, ``infra/deploy.sh`` and the
#: systemd unit the second.
ENV_FILES = (".env", "infra/.env")
#: The settings both must agree on: the range, and the proxies the orchestrator trusts in it.
AGREED = (SETTING, "FORWARDED_ALLOW_IPS")
#: nginx's and the dashboard tunnel's fixed addresses, from the start of the range.
_FIXED_OFFSETS = (10, 11)
_MOVE = (
    f"set {SETTING} to the first two octets of a free private /16, for example 10.229, in .env and, when it exists, "
    "in infra/.env"
)

Run = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class Finding:
    """What the check found: problems refuse the start; notes are printed and the start goes on."""

    problems: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def env_value(path: Path, key: str) -> str | None:
    """*key*'s value in the dotenv file at *path*, as compose reads it; None when the file or the key is absent, or
    the value is empty (compose's ``${KEY:-default}`` then gives the default). The last line wins; ``export`` and
    matching quotes are allowed, and a comment after the value (``  # ...``) is not part of it."""
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return None
    found: str | None = None
    for line in text.splitlines():
        m = re.fullmatch(rf"\s*(?:export\s+)?{re.escape(key)}\s*=\s*(.*?)\s*", line)
        if m is None:
            continue
        value = m.group(1)
        quoted = re.fullmatch(r"""(["'])(.*?)\1\s*(?:#.*)?""", value)
        value = quoted.group(2) if quoted else re.split(r"\s+#", value, maxsplit=1)[0].strip()
        found = value or None
    return found


def disagreements(root: Path) -> list[str]:
    """A problem for each setting the two env files set differently, when both exist."""
    files = [root / name for name in ENV_FILES]
    if not all(f.is_file() for f in files):
        return []
    problems = []
    for key in AGREED:
        first, second = (env_value(f, key) for f in files)
        if first != second:
            shown = [repr(v) if v is not None else "unset" for v in (first, second)]
            problems.append(
                f"{key} is {shown[0]} in {ENV_FILES[0]} and {shown[1]} in {ENV_FILES[1]}: `embry0 start` reads the "
                f"first, and infra/deploy.sh and the systemd unit read the second, so the stack's range and the "
                f"proxies it trusts would depend on how it was started. Set the same value in both."
            )
    return problems


def _subnets(config: Mapping[str, Any]) -> list[str]:
    network = (config.get("networks") or {}).get(NETWORK) or {}
    return [str(c["subnet"]) for c in ((network.get("ipam") or {}).get("config") or []) if c.get("subnet")]


def _ip_subnets(net: Mapping[str, Any]) -> list[Any]:
    found = []
    for cfg in (net.get("IPAM") or {}).get("Config") or []:
        try:
            found.append(ipaddress.ip_network(cfg["Subnet"], False))
        except (KeyError, TypeError, ValueError):
            continue
    return found


def _foreign_subnets(networks: Sequence[Mapping[str, Any]], project: str) -> list[tuple[str, Any]]:
    """(name, network) for each subnet Docker reports on a network that is not this compose project's."""
    return [
        (str(net.get("Name") or net.get("Id") or "?"), subnet)
        for net in networks
        if (net.get("Labels") or {}).get(_PROJECT_LABEL) != project
        for subnet in _ip_subnets(net)
    ]


def _own_network(networks: Sequence[Mapping[str, Any]], project: str) -> Mapping[str, Any] | None:
    """This project's frontend network, when Docker has it."""
    for net in networks:
        labels = net.get("Labels") or {}
        if labels.get(_PROJECT_LABEL) == project and labels.get(_NETWORK_LABEL) == NETWORK:
            return net
    return None


def _inside(net: Any, ranges: Sequence[Any]) -> bool:
    """Whether *net* lies inside one of *ranges* of its own IP version."""
    return any(net.version == r.version and net.subnet_of(r) for r in ranges)


def _forwarded_notes(config: Mapping[str, Any], ranges: Sequence[Any]) -> list[str]:
    """A note for each proxy FORWARDED_ALLOW_IPS trusts beyond nginx and the dashboard tunnel."""
    env = ((config.get("services") or {}).get("orchestrator") or {}).get("environment") or {}
    raw = env.get("FORWARDED_ALLOW_IPS") if isinstance(env, Mapping) else None
    fixed = {r.network_address + offset for r in ranges for offset in _FIXED_OFFSETS}
    keyed = "can set the client address the rate limiter and the audit trail key on"
    unset = f"Unset, it trusts nginx and the dashboard tunnel only, following {SETTING}."
    notes = []
    for entry in (e.strip() for e in str(raw or "").split(",")):
        if not entry:
            continue
        if entry == "*":
            notes.append(
                f"FORWARDED_ALLOW_IPS trusts every peer ('*'): anything that reaches the orchestrator {keyed}. {unset}"
            )
            continue
        try:
            trusted = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        if not _inside(trusted, ranges):
            where = ", ".join(map(str, ranges))
            notes.append(
                f"FORWARDED_ALLOW_IPS trusts {entry}, which is not on the {NETWORK} network ({where}): the "
                f"orchestrator then takes nginx's and the tunnel's address for every client's. {unset}"
            )
        elif trusted.num_addresses > sum(1 for address in fixed if address in trusted):
            notes.append(
                f"FORWARDED_ALLOW_IPS trusts {entry}, which holds addresses other than nginx's and the dashboard "
                f"tunnel's: any other container on the {NETWORK} network {keyed}. {unset}"
            )
    return notes


def check(config: Mapping[str, Any], networks: Sequence[Mapping[str, Any]], *, project: str) -> Finding:
    """The verdict on the rendered compose *config*, given the *networks* Docker reports (``docker network inspect``)."""
    project = str(config.get("name") or project)
    subnets = _subnets(config)
    if not subnets:
        return Finding(
            notes=(f"the compose config names no subnet for the {NETWORK} network: its range is not checked",)
        )
    problems: list[str] = []
    ranges = []
    for raw in subnets:
        try:
            wanted = ipaddress.ip_network(raw)
        except ValueError:
            problems.append(f"{SETTING} gives the {NETWORK} network {raw}, which is not a network: {_MOVE}")
            continue
        ranges.append(wanted)
        if not _inside(wanted, PRIVATE_RANGES):
            problems.append(
                f"the {NETWORK} network {wanted} is outside the private ranges ({_PRIVATE_TEXT}), which sandboxes "
                f"are kept from: {_MOVE}"
            )
            continue
        for name, other in _foreign_subnets(networks, project):
            if other.version == wanted.version and other.overlaps(wanted):
                problems.append(
                    f"the {NETWORK} network {wanted} overlaps {other} (network {name}), so Docker cannot create it: "
                    f"remove that network if nothing uses it, or {_MOVE}"
                )
    own = _own_network(networks, project)
    if own is not None and ranges and sorted(map(str, _ip_subnets(own))) != sorted(map(str, ranges)):
        have = ", ".join(map(str, _ip_subnets(own))) or "no subnet"
        problems.append(
            f"the stack's network {own.get('Name') or NETWORK} has {have}, but {SETTING} now gives "
            f"{', '.join(map(str, ranges))}: its services would get addresses the network does not hold. Set "
            f"{SETTING} to match the network, or move it: `embry0 stop` (or `docker compose down` from infra/) "
            "removes the network, and the next start creates it on the new range."
        )
    notes = _forwarded_notes(config, ranges) if ranges and not problems else []
    return Finding(problems=tuple(problems), notes=tuple(notes))


def inspect(
    compose_cmd: Sequence[str], *, project: str, root: Path, cwd: Path | None = None, run: Run | None = None
) -> Finding:
    """Check the env files under *root* agree, then render the compose config *compose_cmd* names (run in *cwd*) and
    check it against Docker's networks.

    A rendering or a network listing that cannot be read is a note, not a problem: the build and ``up`` that come
    next report their own error, and a check that could not run says so instead of passing.
    """
    run = run or subprocess.run
    agreed = tuple(disagreements(root))
    rendered = run([*compose_cmd, "config", "--format", "json"], capture_output=True, text=True, check=False, cwd=cwd)
    try:
        config = json.loads(rendered.stdout) if rendered.returncode == 0 else None
    except ValueError:
        config = None
    if not isinstance(config, dict):
        note = f"could not render the compose config: the {NETWORK} network's range is not checked"
        return Finding(problems=agreed, notes=(note,))
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
        note = f"could not list Docker's networks: the {NETWORK} network's range is not checked"
        return Finding(problems=agreed, notes=(note,))
    found = check(config, networks, project=project)
    return Finding(problems=agreed + found.problems, notes=found.notes)


def main(argv: Sequence[str] | None = None, run: Run | None = None) -> int:
    """The check as ``infra/deploy.sh`` runs it: compose from ``infra/``, as the script and the systemd unit run it.
    Prints each problem and note to stderr; exits 1 on a problem."""
    parser = argparse.ArgumentParser(description="Check the dashboard network's range before a deploy builds.")
    parser.add_argument("--root", type=Path, required=True, help="the embry0 checkout")
    parser.add_argument("--project", default="embry0", help="the compose project, when the config does not name it")
    args = parser.parse_args(argv)
    found = inspect(["docker", "compose"], project=args.project, root=args.root, cwd=args.root / "infra", run=run)
    for note in found.notes:
        print(f"frontend network: {note}", file=sys.stderr)
    for problem in found.problems:
        print(f"frontend network: {problem}", file=sys.stderr)
    return 1 if found.problems else 0


if __name__ == "__main__":  # pragma: no cover - run by infra/deploy.sh
    raise SystemExit(main())
