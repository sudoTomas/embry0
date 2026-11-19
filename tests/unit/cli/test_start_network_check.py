"""`embry0 start` checks the dashboard network's range before it builds (#366).

Docker refuses to create the pinned `frontend` network over part of another
network's range, and compose finds that out at `up`, after every image is
built. The start reads the range from compose's own rendering, with the files
and env files `up` uses, and refuses before the build when another network
overlaps it or it lies outside the private ranges, naming the network and the
setting that moves the range. Nothing here touches Docker, except the
parse-only `docker compose config` of the last test.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from embry0.cli import __main__ as cli

REPO_ROOT = Path(__file__).resolve().parents[3]
_REQUIRED = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*):?\?")


def _net(name: str, subnet: str, project: str | None = None) -> dict[str, Any]:
    labels = {"com.docker.compose.project": project} if project else {}
    return {"Id": f"id-{name}", "Name": name, "Labels": labels, "IPAM": {"Config": [{"Subnet": subnet}]}}


def _config(prefix: str, forwarded: str | None = None) -> dict[str, Any]:
    """The part of compose's rendering the check reads (the last test reads the real one)."""
    trusted = forwarded if forwarded is not None else f"{prefix}.0.10,{prefix}.0.11"
    return {
        "name": "embry0",
        "networks": {
            "frontend": {"name": "embry0_frontend", "ipam": {"config": [{"subnet": f"{prefix}.0.0/16"}]}},
            "backend": {"name": "embry0_backend"},
        },
        "services": {"orchestrator": {"environment": {"FORWARDED_ALLOW_IPS": trusted}}},
    }


class Docker:
    """Answers every command the CLI runs, and records it. Images are missing, so a start that goes on builds."""

    def __init__(self, networks: list[dict[str, Any]], render: Callable[[list[str]], Any]) -> None:
        self.networks = networks
        self.render = render
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(cmd))
        done = subprocess.CompletedProcess
        if cmd[:2] == ["docker", "compose"] and "config" in cmd:
            return self.render(cmd)
        if cmd[:3] == ["docker", "network", "ls"]:
            return done(cmd, 0, stdout="\n".join(n["Id"] for n in self.networks) + "\n", stderr="")
        if cmd[:3] == ["docker", "network", "inspect"]:
            return done(cmd, 0, stdout=json.dumps([n for n in self.networks if n["Id"] in cmd[3:]]), stderr="")
        if cmd[:3] == ["docker", "image", "inspect"]:
            return done(cmd, 1, stdout="", stderr="")
        return done(cmd, 0, stdout="", stderr="")

    def builds(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["docker", "compose"] and "build" in c]

    def ups(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["docker", "compose"] and "up" in c]


def _rendered(config: dict[str, Any] | None) -> Callable[[list[str]], Any]:
    def render(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if config is None:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no such file")
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(config), stderr="")

    return render


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "infra").mkdir()
    shutil.copy(REPO_ROOT / "infra" / "docker-compose.yml", tmp_path / "infra" / "docker-compose.yml")
    (tmp_path / ".env").write_text("POSTGRES_PASSWORD=x\n")
    monkeypatch.setenv("EMBRY0_HOME", str(tmp_path))
    return tmp_path


def _start(monkeypatch: pytest.MonkeyPatch, docker: Docker) -> int | None:
    """Run `embry0 start`; the exit code when it exits, None when it ran to the end."""
    monkeypatch.setattr(cli, "_run", lambda cmd, **kw: docker(cmd, **kw))
    monkeypatch.setattr(cli.subprocess, "run", docker)
    monkeypatch.setattr(cli, "_docker_available", lambda: True)
    monkeypatch.setattr(cli, "_stack_running", lambda _home: False)
    monkeypatch.setattr(cli, "_wait_for_dind", lambda: True)
    try:
        cli.cmd_start(argparse.Namespace(port=None, host=None))
    except SystemExit as exc:
        return int(exc.code or 0)
    return None


TAKEN = [_net("bridge", "172.17.0.0/16"), _net("accounts", "172.29.240.0/24")]


def test_start_refuses_a_taken_range_before_it_builds(home, monkeypatch, capsys) -> None:
    docker = Docker(TAKEN, _rendered(_config("172.29")))
    code = _start(monkeypatch, docker)
    err = capsys.readouterr().err
    assert code == 1 and docker.builds() == [] and docker.ups() == [], (
        f"a start over a taken range must stop before the build: exit {code}, builds {docker.builds()}"
    )
    assert "172.29.240.0/24 (network accounts)" in err, err
    assert "FRONTEND_NETWORK_PREFIX" in err, f"the refusal does not name the setting that moves the range: {err}"


def test_the_check_reads_the_config_up_uses(home, monkeypatch) -> None:
    docker = Docker(TAKEN, _rendered(_config("172.29")))
    _start(monkeypatch, docker)
    configs = [c for c in docker.calls if c[:2] == ["docker", "compose"] and "config" in c]
    assert configs == [cli._compose_cmd(home) + ["config", "--format", "json"]], (
        f"the range is read with other files or env files than `up` reads: {configs}"
    )


def test_start_does_not_count_the_stacks_own_network_as_taken(home, monkeypatch) -> None:
    own = [_net("bridge", "172.17.0.0/16"), _net("embry0_frontend", "172.29.0.0/16", project="embry0")]
    docker = Docker(own, _rendered(_config("172.29")))
    assert _start(monkeypatch, docker) is None and docker.builds(), "the stack's own network is not a clash"


def test_start_builds_on_a_range_moved_off_the_taken_one(home, monkeypatch, capsys) -> None:
    docker = Docker(TAKEN, _rendered(_config("10.229")))
    assert _start(monkeypatch, docker) is None and docker.builds() and docker.ups(), capsys.readouterr().err


@pytest.mark.parametrize("prefix", ["8.8", "11.0", "172.15", "172.32", "192.169", "100.64", "169.254"])
def test_start_refuses_a_range_outside_the_private_ones(home, monkeypatch, capsys, prefix: str) -> None:
    """The egress chain keeps sandboxes off the private ranges (egress.py); a public range would also hide real
    addresses from the host, and the tailnet and link-local ranges are not Docker's to hand out."""
    docker = Docker([_net("bridge", "172.17.0.0/16")], _rendered(_config(prefix)))
    code = _start(monkeypatch, docker)
    err = capsys.readouterr().err
    assert code == 1 and docker.builds() == [], f"{prefix}.0.0/16 is not a private range, yet the start went on"
    assert "10.0.0.0/8, 172.16.0.0/12 or 192.168.0.0/16" in err, err


def test_start_refuses_a_prefix_that_is_not_two_octets(home, monkeypatch, capsys) -> None:
    docker = Docker([_net("bridge", "172.17.0.0/16")], _rendered(_config("10.229.1")))
    code = _start(monkeypatch, docker)
    err = capsys.readouterr().err
    assert code == 1 and docker.builds() == [], "10.229.1.0.0/16 is not a network, yet the start went on"
    assert "FRONTEND_NETWORK_PREFIX" in err and "10.229.1.0.0/16" in err, err


def test_start_warns_when_forwarded_allow_ips_trusts_an_address_off_the_network(home, monkeypatch, capsys) -> None:
    """An explicit FORWARDED_ALLOW_IPS left on the old addresses after the range moved: the orchestrator would trust
    neither proxy and every request would come from nginx. That degrades, it does not break: start, and say so."""
    docker = Docker(TAKEN, _rendered(_config("10.229", forwarded="172.29.0.10,172.29.0.11")))
    code = _start(monkeypatch, docker)
    err = capsys.readouterr().err
    assert code is None and docker.ups(), err
    assert "FORWARDED_ALLOW_IPS" in err and "172.29.0.10" in err and "10.229.0.0/16" in err, (
        f"no warning that the orchestrator trusts addresses off the frontend network: {err}"
    )


def test_start_says_when_it_cannot_check_the_range_and_goes_on(home, monkeypatch, capsys) -> None:
    docker = Docker(TAKEN, _rendered(None))
    code = _start(monkeypatch, docker)
    err = capsys.readouterr().err
    assert code is None and docker.ups(), err
    assert "not checked" in err, f"a check that could not run must say so, not pass in silence: {err}"


def _compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _compose_available(), reason="needs the docker compose CLI")
@pytest.mark.parametrize(("prefix", "refused"), [(None, True), ("10.229", False)])
def test_the_check_reads_compose_s_real_rendering(home, monkeypatch, capsys, prefix: str | None, refused) -> None:
    """The check against the real parser's output, so a key it reads that compose does not write cannot pass as a
    free range. Parse-only: DOCKER_HOST points at nothing; every other command is answered by the fake."""
    compose = (home / "infra" / "docker-compose.yml").read_text()
    lines = [f"{name}=x\n" for name in sorted(set(_REQUIRED.findall(compose)))]
    (home / ".env").write_text("".join(lines) + (f"FRONTEND_NETWORK_PREFIX={prefix}\n" if prefix else ""))
    real_run = subprocess.run

    def render(cmd: list[str]) -> Any:
        env = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")}
        env.pop("FRONTEND_NETWORK_PREFIX", None)
        env["DOCKER_HOST"] = f"unix://{home}/no-daemon.sock"
        return real_run(cmd, capture_output=True, text=True, env=env)

    docker = Docker(TAKEN, render)
    code = _start(monkeypatch, docker)
    err = capsys.readouterr().err
    if refused:
        assert code == 1 and "172.29.240.0/24 (network accounts)" in err, f"exit {code}: {err}"
    else:
        assert code is None and docker.ups() and "not checked" not in err, f"exit {code}: {err}"
