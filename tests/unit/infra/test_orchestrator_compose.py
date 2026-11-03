"""The orchestrator image has `docker compose`, at the release the QA image uses (#342).

The QA routes run `docker compose` from the orchestrator against a QA
workspace's project in DinD: the service list (`compose_services`) and the
live log (`compose_log_stream`). The compose plugin is only a recommended
package of docker-ce-cli, and the Dockerfile installs with
--no-install-recommends, so the image had none: the service list was always
empty and the live log ended at once.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from tests.unit.infra.test_orchestrator_image_scan import _instructions

ROOT = Path(__file__).resolve().parents[3]
ORCHESTRATOR = ROOT / "infra" / "Dockerfile.orchestrator"
QA = ROOT / "infra" / "Dockerfile.sandbox.qa"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
IMAGE = "embry0-orchestrator:ci"

_PLUGIN = re.compile(r"\bdocker-compose-plugin(?:=(\S+))?")
_QA_RELEASE = re.compile(r"docker/compose/releases/download/v([0-9][^/]*)/")


def test_the_orchestrator_installs_the_compose_plugin_at_the_qa_image_s_release() -> None:
    installs = [m.group(1) for ins in _instructions(ORCHESTRATOR) for m in _PLUGIN.finditer(ins)]
    assert installs, (
        "Dockerfile.orchestrator installs no docker-compose-plugin: it is only a recommended package of "
        "docker-ce-cli, and --no-install-recommends leaves it out, so the QA routes' `docker compose` fails"
    )
    assert len(installs) == 1 and installs[0], f"the compose plugin is installed once, at an exact version: {installs}"
    qa = {m.group(1) for ins in _instructions(QA) for m in _QA_RELEASE.finditer(ins)}
    assert len(qa) == 1, f"Dockerfile.sandbox.qa downloads one compose release: {qa}"
    upstream = installs[0].split("-", 1)[0]
    assert {upstream} == qa, (
        f"the orchestrator's compose ({installs[0]}) and the QA image's ({qa}) read one workspace's project: "
        "keep them at one release"
    )


def _steps() -> dict[str, list[dict[str, Any]]]:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    return {name: list(job.get("steps") or []) for name, job in jobs.items()}


def test_ci_runs_docker_compose_in_the_built_orchestrator_image() -> None:
    found = []
    for job, steps in _steps().items():
        for index, step in enumerate(steps):
            if f"{IMAGE} docker compose version" not in str(step.get("run", "")):
                continue
            loaded = any(
                (earlier.get("with") or {}).get("tags") == IMAGE and (earlier.get("with") or {}).get("load") is True
                for earlier in steps[:index]
            )
            found.append((job, loaded))
    assert found, f"no CI step runs `docker compose version` in {IMAGE}"
    assert all(loaded for _job, loaded in found), f"a step runs {IMAGE} before its job loads it: {found}"
