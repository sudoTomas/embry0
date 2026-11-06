"""A stand-in for iptables and iptables-restore, for tests of the egress helper scripts.

The scripts the egress enforcer ships to the DinD helper run under ``sh``
against this fake instead of being matched as strings (#566, #568), and
``FakeFilterTable.verdict`` walks the resulting rules for a packet.
"""

from __future__ import annotations

import ipaddress
import json
import os
import shlex
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock

# A stand-in for iptables that keeps the filter table's chains in a file and
# prints them the way iptables does for -S and for -L --line-numbers -n, so the
# helper scripts run for real instead of being matched as strings (#566).
# ``iptables-restore --noflush`` replaces each chain it declares, as iptables
# does; FAKE_RESTORE_BROKEN makes it write nothing.
_FAKE_IPTABLES = r"""
import json, os, shlex, sys

state = os.environ["FAKE_IPTABLES_STATE"]
chains = json.load(open(state)) if os.path.exists(state) else {"DOCKER-USER": []}
BUILTIN = {"RETURN", "DROP", "ACCEPT", "LOG"}


def normalized(chain, spec):
    for i, word in enumerate(spec[:-1]):  # iptables stores a bare address as /32
        if word in ("-s", "-d") and "/" not in spec[i + 1]:
            spec[i + 1] += "/32"
    if "-j" in spec and spec[spec.index("-j") + 1] not in BUILTIN | set(chains):
        sys.exit(f"iptables: Couldn't load target `{spec[spec.index('-j') + 1]}'")
    return f"-A {chain} " + shlex.join(spec)


if os.path.basename(sys.argv[0]) == "iptables-restore":
    lines = sys.stdin.read().splitlines()
    if not os.environ.get("FAKE_RESTORE_BROKEN"):
        for line in lines:
            if line.startswith(":"):
                chains[line[1:].split()[0]] = []
            elif line.startswith("-A "):
                words = shlex.split(line)
                chains[words[1]].append(normalized(words[1], words[2:]))
else:
    args = sys.argv[1:]
    if args[:1] == ["-I"]:
        chains[args[1]].insert(int(args[2]) - 1, normalized(args[1], args[3:]))
    elif args[:1] == ["-D"]:
        del chains[args[1]][int(args[2]) - 1]
    elif args[:1] == ["-S"]:
        if args[1] not in chains:
            sys.exit(f"iptables: No chain/target/match by that name.")
        for line in [f"-N {args[1]}", *chains[args[1]]]:
            print(line)
    elif args[:1] == ["-L"]:
        print("Chain DOCKER-USER (1 references)")
        print("num  target     prot opt source               destination")
        for num, rule in enumerate(chains["DOCKER-USER"], 1):
            f = shlex.split(rule)
            get = lambda flag: f[f.index(flag) + 1] if flag in f else "0.0.0.0/0"
            src, dst = (get(x).removesuffix("/32") for x in ("-s", "-d"))
            print(f"{num:<4} {get('-j'):<10} all  --  {src:<20} {dst}")
    else:
        sys.exit(f"fake iptables: unsupported {args}")
with open(state, "w") as out:
    json.dump(chains, out)
"""


class FakeFilterTable:
    """The fake's state file, read back."""

    def __init__(self, path) -> None:
        self.path = path

    def chains(self) -> dict[str, list[str]]:
        return json.loads(self.path.read_text()) if self.path.exists() else {"DOCKER-USER": []}

    def chain(self, name: str = "DOCKER-USER") -> list[str]:
        return self.chains().get(name, [])

    def seed(self, chains: dict[str, list[str]]) -> None:
        self.path.write_text(json.dumps(chains))

    def verdict(self, src: str, dst: str, *, established: bool = False) -> str:
        """``pass`` or ``drop`` for a forwarded packet, walking DOCKER-USER as the kernel does.

        A RETURN in DOCKER-USER hands the packet back to FORWARD (``pass``); a
        RETURN in a chain DOCKER-USER jumped to comes back to DOCKER-USER.
        Interface-matched rules (the isolation jump) never match here: these
        packets stay on sandbox-internet.
        """
        chains = self.chains()

        def matches(words: list[str]) -> bool:
            if "-i" in words or "-o" in words:
                return False
            for flag, address in (("-s", src), ("-d", dst)):
                if flag in words and ipaddress.ip_address(address) not in ipaddress.ip_network(
                    words[words.index(flag) + 1]
                ):
                    return False
            return established or "--ctstate" not in words

        def walk(name: str) -> str:
            for rule in chains.get(name, []):
                words = shlex.split(rule)[2:]
                if not matches(words):
                    continue
                target = words[words.index("-j") + 1]
                if target == "LOG":
                    continue
                if target in ("RETURN", "DROP"):
                    return target
                if walk(target) == "DROP":
                    return "DROP"
            return "END"

        return "drop" if walk("DOCKER-USER") == "DROP" else "pass"


def _chain_docker(tmp_path, *, restore_broken: bool = False) -> tuple[MagicMock, FakeFilterTable]:
    """A DockerClient mock that runs each helper script against the fake filter table."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for tool in ("iptables", "iptables-restore"):
        fake = bindir / tool
        fake.write_text(f"#!{sys.executable}\n{_FAKE_IPTABLES}")
        fake.chmod(0o755)
    state = tmp_path / "filter.json"
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_IPTABLES_STATE": str(state)}
    if restore_broken:
        env["FAKE_RESTORE_BROKEN"] = "1"

    async def run_cmd(cmd: list[str], timeout: int = 30, *, max_bytes: int | None = None) -> str:
        done = subprocess.run(["sh", "-c", cmd[-1]], env=env, capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError(f"Docker command failed: {done.stderr.strip()}")
        return done.stdout

    docker = MagicMock()
    docker._build_base_cmd = MagicMock(side_effect=lambda: ["docker", "--host", "tcp://dind:2376"])
    docker.run_cmd = AsyncMock(side_effect=run_cmd)
    return docker, FakeFilterTable(state)
