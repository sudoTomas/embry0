"""Guard: infra/docker-compose.rootless.yml (AGENT_RUNTIME=rootless, #268).

The overlay replaces the orchestrator's environment, volumes and depends_on
whole (compose `!override`), so it restates what it keeps from the base file.
A key added to the base file later would silently go missing under rootless;
these tests hold the restated parts equal to the base minus exactly what the
rootless runtime drops. They also pin the one socket path that four files
must agree on, and that the DinD-only services are really off.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
INFRA = REPO_ROOT / "infra"
BASE = INFRA / "docker-compose.yml"
OVERLAY = INFRA / "docker-compose.rootless.yml"
DAEMON_JSON = INFRA / "rootless" / "daemon.json"
PROVISION = INFRA / "rootless" / "provision-sandbox-daemon.sh"
INFRA_ENV_EXAMPLE = INFRA / ".env.example"

SOCKET_DIR = "/run/embry0-sandbox"
SOCKET = f"unix://{SOCKET_DIR}/docker.sock"

# What the rootless runtime does not use: the DinD daemon's TLS, and the
# registry login and pins (#523) that only exist for the in-stack registry.
DROPPED_ENV = {"DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "REGISTRY_AUTH_DIR", "IMAGE_PINS_FILE"}
DROPPED_VOLUME_SOURCES = {"dind-certs-client", "registry-auth", "image-pins"}
DROPPED_DEPENDENCIES = {"dind", "init-push-images"}
SWITCHED_OFF = {"dind", "registry", "registry-auth-init", "init-push-images"}

# Spec 3.2: the skills store. The rootless daemon resolves bind sources on the
# host, where the base file's named volume has no path it can name, so the
# overlay swaps the volume for a host directory and tells the orchestrator
# that directory (SKILLS_STORE_DAEMON_PATH) to build the sandbox mounts from.
SKILLS_HOST = "${SKILLS_STORE_HOST:-/var/lib/embry0/skills}"
SKILLS_VOLUME = "skills-store:/skills"
SKILLS_BIND = f"{SKILLS_HOST}:/skills"


class _Loader(yaml.SafeLoader):
    """Reads compose's `!override` / `!reset` merge tags as the plain value."""


def _tagged(loader: yaml.SafeLoader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)  # type: ignore[arg-type]


for _tag in ("!override", "!reset"):
    _Loader.add_constructor(_tag, _tagged)


def _services(path: Path) -> dict[str, Any]:
    return yaml.load(path.read_text(), Loader=_Loader)["services"]  # noqa: S506 — a SafeLoader subclass


def _volume_source(entry: str) -> str:
    return entry.split(":", 1)[0]


def test_the_overlay_keeps_every_base_orchestrator_variable_but_the_dind_ones():
    base = _services(BASE)["orchestrator"]["environment"]
    overlay = _services(OVERLAY)["orchestrator"]["environment"]
    kept = {k: v for k, v in base.items() if k not in DROPPED_ENV and k != "DOCKER_HOST"}
    restated = {k: v for k, v in overlay.items() if k in kept}
    assert restated == kept, (
        "docker-compose.rootless.yml replaces the orchestrator's environment whole; restate every "
        f"base variable except {sorted(DROPPED_ENV)} unchanged. Differs: "
        f"{sorted(k for k in kept if overlay.get(k) != kept[k])}"
    )
    assert DROPPED_ENV.isdisjoint(overlay), (
        f"{sorted(DROPPED_ENV & set(overlay))} must be absent, not blank: the Docker CLI turns TLS on "
        "for any non-empty DOCKER_TLS_VERIFY, and pins without a registry refuse the start"
    )
    assert overlay["AGENT_RUNTIME"] == "rootless"
    assert overlay["DOCKER_HOST"] == SOCKET
    assert overlay["IMAGE_REGISTRY"] == "", "the root .env's IMAGE_REGISTRY must be overridden empty"
    assert overlay["SKILLS_STORE_DAEMON_PATH"] == SKILLS_HOST, (
        "the sandbox daemon must be told the host directory the store is bind-mounted from"
    )
    assert set(overlay) == set(kept) | {"AGENT_RUNTIME", "DOCKER_HOST", "IMAGE_REGISTRY", "SKILLS_STORE_DAEMON_PATH"}


def test_the_overlay_keeps_every_base_orchestrator_mount_but_the_dind_ones():
    base = _services(BASE)["orchestrator"]["volumes"]
    overlay = _services(OVERLAY)["orchestrator"]["volumes"]
    assert SKILLS_VOLUME in base, "the base file's skills store mount moved; update SKILLS_VOLUME"
    kept = [v for v in base if _volume_source(v) not in DROPPED_VOLUME_SOURCES and v != SKILLS_VOLUME]
    assert [v for v in overlay if v in kept] == kept, (
        f"base mounts missing from the overlay: {set(kept) - set(overlay)}"
    )
    assert f"{SOCKET_DIR}:{SOCKET_DIR}:ro" in overlay, "the sandbox daemon's socket directory is not mounted"
    assert SKILLS_BIND in overlay and SKILLS_VOLUME not in overlay, (
        "the skills store must be a host directory the rootless daemon can bind-mount from"
    )
    assert set(overlay) == set(kept) | {f"{SOCKET_DIR}:{SOCKET_DIR}:ro", SKILLS_BIND}


def test_the_overlay_keeps_every_base_dependency_but_the_dind_ones():
    base = _services(BASE)["orchestrator"]["depends_on"]
    overlay = _services(OVERLAY)["orchestrator"]["depends_on"]
    assert overlay == {k: v for k, v in base.items() if k not in DROPPED_DEPENDENCIES}


def test_the_orchestrator_joins_the_socket_group():
    assert any("SANDBOX_DOCKER_GID" in str(g) for g in _services(OVERLAY)["orchestrator"]["group_add"])


def test_the_dind_only_services_are_switched_off():
    overlay = _services(OVERLAY)
    for name in SWITCHED_OFF:
        assert overlay[name].get("profiles") == ["dind-runtime"], f"{name} still starts under the rootless overlay"
    documented = INFRA_ENV_EXAMPLE.read_text()
    assert "dind-runtime" not in documented, "the switch-off profile must never be an operator-facing profile"


def test_networks_are_created_on_the_sandbox_daemon():
    init = _services(OVERLAY)["init-sandbox-networks"]
    assert init["environment"] == {"DOCKER_HOST": SOCKET}
    assert f"{SOCKET_DIR}:{SOCKET_DIR}:ro" in init["volumes"]


def test_one_socket_path_everywhere():
    daemon = json.loads(DAEMON_JSON.read_text())
    assert daemon["hosts"] == [SOCKET], "the daemon must listen on the unix socket only, never TCP"
    assert re.search(rf"^SOCKET_DIR={re.escape(SOCKET_DIR)}$", PROVISION.read_text(), re.MULTILINE)


def test_every_overlay_variable_has_a_template_entry():
    referenced = set(re.findall(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)", OVERLAY.read_text()))
    documented = set(re.findall(r"^#?\s*([A-Z_][A-Z0-9_]*)=", INFRA_ENV_EXAMPLE.read_text(), re.MULTILINE))
    assert referenced - documented == set(), f"undocumented in infra/.env.example: {referenced - documented}"


def _compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _compose_available(), reason="needs the docker compose CLI")
def test_compose_merges_the_overlay(tmp_path: Path) -> None:
    """The real merge: what `docker compose up` would start, and with what."""
    # A copy, beside an empty root .env: `config --format json` reads the
    # orchestrator's env_file (../.env), which a checkout does not have.
    infra = tmp_path / "infra"
    infra.mkdir()
    for path in (BASE, OVERLAY):
        shutil.copy(path, infra / path.name)
    (tmp_path / ".env").write_text("")
    env_file = tmp_path / "env"
    env_file.write_text("POSTGRES_PASSWORD=x\nMINIO_ROOT_USER=x\nMINIO_ROOT_PASSWORD=x\nSANDBOX_DOCKER_GID=1234\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")}
    env["DOCKER_HOST"] = f"unix://{tmp_path}/no-daemon.sock"
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(infra / BASE.name),
            "-f",
            str(infra / OVERLAY.name),
            "--env-file",
            str(env_file),
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"compose refused the overlay:\n{result.stderr}"
    services = json.loads(result.stdout)["services"]
    assert SWITCHED_OFF.isdisjoint(services), f"still started: {SWITCHED_OFF & set(services)}"
    orchestrator = services["orchestrator"]
    assert DROPPED_ENV.isdisjoint(orchestrator["environment"])
    assert orchestrator["environment"]["DOCKER_HOST"] == SOCKET
    assert orchestrator["group_add"] == ["1234"]
    assert DROPPED_DEPENDENCIES.isdisjoint(orchestrator["depends_on"])
    assert {v["source"] for v in orchestrator["volumes"]}.isdisjoint(DROPPED_VOLUME_SOURCES)
    [skills] = [v for v in orchestrator["volumes"] if v["target"] == "/skills"]
    assert skills["type"] == "bind" and skills["source"] == "/var/lib/embry0/skills"
    assert orchestrator["environment"]["SKILLS_STORE_DAEMON_PATH"] == skills["source"]
