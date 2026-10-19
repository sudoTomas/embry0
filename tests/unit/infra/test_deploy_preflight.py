"""infra/deploy.sh's preflight: what it refuses, and what it recreates.

The script runs from a copy of its layout with docker, git and curl stubbed
on PATH; each stub logs its arguments, so nothing reaches a daemon or a
stack.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "infra" / "deploy.sh"

_STUBS = {
    "docker": """#!/bin/sh
echo "docker $*" >> "$STUB_LOG"
case "$*" in *"${STUB_DOCKER_FAIL:-@never@}"*) exit 1 ;; esac
""",
    "git": """#!/bin/sh
echo "git $*" >> "$STUB_LOG"
case "$*" in
  *"rev-parse --abbrev-ref HEAD"*) echo main ;;
  *"rev-parse --short=12 HEAD"*) echo 0123456789ab ;;
  *"status --porcelain"*) if [ -n "${STUB_GIT_STATUS_FILE-}" ]; then cat "$STUB_GIT_STATUS_FILE"; else printf '%s' "${STUB_GIT_STATUS-}"; fi ;;
esac
""",
    # The running-jobs query answers with STUB_JOBS_BODY and the status in
    # STUB_JOBS_HTTP (000: nothing listening); every other URL is healthy.
    "curl": """#!/bin/sh
echo "curl $*" >> "$STUB_LOG"
case "$*" in
  *"jobs?status=running"*)
    code="${STUB_JOBS_HTTP:-200}"
    case "$*" in *"%{http_code}"*) printf '%s\\n%s' "${STUB_JOBS_BODY-}" "$code" ;; esac
    [ "$code" = 200 ] || exit 22
    case "$*" in *"%{http_code}"*) ;; *) printf '%s' "${STUB_JOBS_BODY-}" ;; esac
    ;;
esac
exit 0
""",
}


@pytest.fixture
def deploy(tmp_path: Path):
    root = tmp_path / "checkout"
    (root / "infra").mkdir(parents=True)
    shutil.copy(_SCRIPT, root / "infra" / "deploy.sh")
    (root / "infra" / ".env").write_text("PROD_PORT=8200\n")
    (root / ".env").write_text("API_KEY=test-key\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in _STUBS.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    log = tmp_path / "calls.log"

    def run(*args: str, **env: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        log.write_text("")
        result = subprocess.run(
            ["bash", str(root / "infra" / "deploy.sh"), *args],
            env={
                "PATH": f"{bin_dir}:{os.defpath}",
                "STUB_LOG": str(log),
                "EMBRY0_DEPLOY_DRY_RUN": "1",
                **env,
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result, log.read_text().splitlines()

    return run


def _docker(calls: list[str]) -> list[str]:
    return [call for call in calls if call.startswith("docker ")]


def test_bash_parses_the_script() -> None:
    subprocess.run(["bash", "-n", str(_SCRIPT)], check=True)


def test_a_clean_deploy_recreates_only_the_named_services(deploy) -> None:
    result, calls = deploy()
    assert result.returncode == 0, result.stderr
    ups = [call for call in _docker(calls) if " up " in call]
    assert ups == ["docker compose up -d --force-recreate --no-deps orchestrator mcp frontend"], ups
    assert not any("dind" in call for call in _docker(calls)), "a deploy must never name dind"


@pytest.mark.parametrize("status", [" M embry0/config.py\n", "?? embry0/new_module.py\n"])
def test_a_dirty_tree_is_refused_before_anything_builds(deploy, status: str) -> None:
    result, calls = deploy(STUB_GIT_STATUS=status)
    assert result.returncode == 1, f"a dirty tree deployed: {result.stdout}{result.stderr}"
    assert "uncommitted" in result.stderr and status.strip() in result.stderr, result.stderr
    assert _docker(calls) == [], f"docker ran for a dirty tree: {_docker(calls)}"


def test_a_long_dirty_list_still_refuses_with_its_message(deploy, tmp_path: Path) -> None:
    # More than a pipe buffer, so head closes the pipe while printf writes.
    status = tmp_path / "status.txt"
    status.write_text("".join(f"?? scratch/{n}.log\n" for n in range(8000)))
    result, calls = deploy(STUB_GIT_STATUS_FILE=str(status))
    assert result.returncode == 1, f"exit {result.returncode}: {result.stderr[-300:]}"
    assert "EMBRY0_DEPLOY_FORCE=1" in result.stderr, result.stderr[-300:]
    assert _docker(calls) == []


@pytest.mark.parametrize("http", ["401", "500", "000"])
def test_a_running_jobs_check_without_an_answer_refuses(deploy, http: str) -> None:
    result, calls = deploy(STUB_JOBS_HTTP=http)
    assert result.returncode == 1, f"HTTP {http} let the deploy through: {result.stdout}{result.stderr}"
    assert f"HTTP {http}" in result.stderr and "EMBRY0_DEPLOY_FORCE=1" in result.stderr, result.stderr
    assert _docker(calls) == [], f"docker ran after an unanswered check: {_docker(calls)}"


def test_a_running_job_refuses(deploy) -> None:
    result, calls = deploy(STUB_JOBS_BODY='{"jobs": [{"job_id": "j1"}], "total": 1}')
    assert result.returncode == 1 and "a job is running" in result.stderr, result.stderr
    assert _docker(calls) == []


def test_force_deploys_past_the_refusals(deploy) -> None:
    result, calls = deploy(STUB_GIT_STATUS=" M embry0/config.py\n", STUB_JOBS_HTTP="401", EMBRY0_DEPLOY_FORCE="1")
    assert result.returncode == 0, result.stderr
    assert any(" up " in call for call in _docker(calls)), _docker(calls)


# nginx resolves the orchestrator's name once, when it loads its config, and a
# recreated orchestrator can come back on another address. A deploy that
# recreates the orchestrator but not the frontend reloads nginx after it.
_RELOAD = "docker compose exec -T frontend nginx -s reload"


def test_an_orchestrator_only_deploy_reloads_nginx_after_the_recreate(deploy) -> None:
    result, calls = deploy("orchestrator")
    assert result.returncode == 0, result.stderr
    docker = _docker(calls)
    up = docker.index("docker compose up -d --force-recreate --no-deps orchestrator mcp")
    assert _RELOAD in docker[up + 1 :], f"nginx was not reloaded after the recreate: {docker}"


@pytest.mark.parametrize("services", [(), ("frontend",), ("orchestrator", "frontend")])
def test_a_deploy_that_recreates_the_frontend_does_not_reload_it(deploy, services: tuple[str, ...]) -> None:
    result, calls = deploy(*services)
    assert result.returncode == 0, result.stderr
    assert _RELOAD not in _docker(calls), _docker(calls)


def test_a_failed_reload_fails_the_deploy_and_says_what_to_run(deploy) -> None:
    result, _ = deploy("orchestrator", STUB_DOCKER_FAIL="nginx -s reload")
    assert result.returncode == 1, f"a failed reload passed: {result.stdout}{result.stderr}"
    assert "infra/deploy.sh frontend" in result.stderr, result.stderr
