"""The dashboard network's range is one setting, FRONTEND_NETWORK_PREFIX (#366).

The `frontend` network is pinned so the orchestrator can trust X-Forwarded-For
from exactly nginx and the dashboard tunnel, at fixed addresses in it
(RAV-1060, RAV-1069). Its range, the two addresses and the FORWARDED_ALLOW_IPS
default in both compose files were literals, so moving the range off one
another network uses meant editing five places in two files together. They now
all follow one setting, and left unset it gives the range existing deployments
run on: their network is not recreated by an upgrade.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BASE = REPO_ROOT / "infra" / "docker-compose.yml"
ROOTLESS = REPO_ROOT / "infra" / "docker-compose.rootless.yml"
SETTING = "${FRONTEND_NETWORK_PREFIX:-172.29}"
_REQUIRED = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*):?\?")


def test_every_address_of_the_range_follows_the_one_setting() -> None:
    for path, uses in ((BASE, 5), (ROOTLESS, 2)):
        text = path.read_text()
        left = [line.strip() for line in text.replace(SETTING, "").splitlines() if "172.29" in line]
        assert left == [], f"{path.name} still pins part of the range outside FRONTEND_NETWORK_PREFIX: {left}"
        assert text.count(SETTING) == uses, (
            f"{path.name} names FRONTEND_NETWORK_PREFIX {text.count(SETTING)} times; the subnet, nginx's and the "
            f"tunnel's addresses and the two FORWARDED_ALLOW_IPS entries make {uses}"
        )


def _compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0


def _render(tmp_path: Path, files: list[Path], prefix: str | None) -> dict:
    """The real parser's rendering. Parse-only: `config` talks to no daemon, and DOCKER_HOST points at nothing.

    The files are copied so the orchestrator's `env_file: ../.env` names an empty file, not a checkout's secrets."""
    (tmp_path / "infra").mkdir(exist_ok=True)
    (tmp_path / ".env").write_text("")
    copies = [tmp_path / "infra" / f.name for f in files]
    for f, copy in zip(files, copies, strict=True):
        copy.write_text(f.read_text())
    files = copies
    required = sorted({name for f in files for name in _REQUIRED.findall(f.read_text())})
    lines = [f"{name}=x\n" for name in required] + ([f"FRONTEND_NETWORK_PREFIX={prefix}\n"] if prefix else [])
    env_file = tmp_path / "env"
    env_file.write_text("".join(lines))
    env = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")}
    env.pop("FRONTEND_NETWORK_PREFIX", None)
    env.pop("FORWARDED_ALLOW_IPS", None)
    env["DOCKER_HOST"] = f"unix://{tmp_path}/no-daemon.sock"
    cmd = ["docker", "compose", "-p", "embry0", *(a for f in files for a in ("-f", str(f)))]
    cmd += ["--env-file", str(env_file), "--profile", "dashboard-tunnel", "config", "--format", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert result.returncode == 0, f"compose refused to parse:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.mark.skipif(not _compose_available(), reason="needs the docker compose CLI")
@pytest.mark.parametrize(("prefix", "octets"), [(None, "172.29"), ("10.229", "10.229")])
def test_the_rendered_stack_follows_the_setting(tmp_path: Path, prefix: str | None, octets: str) -> None:
    base = _render(tmp_path, [BASE], prefix)
    subnets = [c["subnet"] for c in base["networks"]["frontend"]["ipam"]["config"]]
    assert subnets == [f"{octets}.0.0/16"], f"FRONTEND_NETWORK_PREFIX={prefix} renders the frontend network {subnets}"
    addresses = {
        name: base["services"][name]["networks"]["frontend"]["ipv4_address"]
        for name in ("frontend", "cloudflared-dashboard")
    }
    assert addresses == {"frontend": f"{octets}.0.10", "cloudflared-dashboard": f"{octets}.0.11"}, addresses
    trusted = f"{octets}.0.10,{octets}.0.11"
    for files in ([BASE], [BASE, ROOTLESS]):
        env = _render(tmp_path, files, prefix)["services"]["orchestrator"]["environment"]
        assert env["FORWARDED_ALLOW_IPS"] == trusted, (
            f"{' + '.join(f.name for f in files)}: the orchestrator trusts {env['FORWARDED_ALLOW_IPS']}, "
            f"not nginx and the tunnel at {trusted}"
        )
