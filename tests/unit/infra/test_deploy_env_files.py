"""infra/deploy.sh says which env file is missing instead of exiting silently (#591).

The script reads infra/.env and the root .env through ``sed | tail`` under
``set -euo pipefail``: with a file absent, ``sed`` failed the pipeline and the
script ended with exit 1 and no message. Each test runs a copy of the script
in a scratch checkout on ``main``, with ``docker`` and ``curl`` stubbed on
PATH, so nothing reaches a real daemon or stack.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "infra" / "deploy.sh"

_STUB = '#!/bin/sh\necho "$(basename "$0") $*" >> "$STUB_LOG"\n'


def _checkout(tmp_path: Path, *, infra_env: bool, root_env: bool) -> Path:
    root = tmp_path / "checkout"
    (root / "infra").mkdir(parents=True)
    (root / "infra" / "deploy.sh").write_text(_SCRIPT.read_text())
    if infra_env:
        (root / "infra" / ".env").write_text("PROD_PORT=8200\n")
    if root_env:
        (root / ".env").write_text("API_KEY=\n")
    # As in the repository: the script is committed and the env files are
    # ignored, so the checkout is clean and the dirty-tree check passes.
    (root / ".gitignore").write_text(".env\n")
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], check=True)
    return root


def _deploy(tmp_path: Path, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for tool in ("docker", "curl"):
        (bindir / tool).write_text(_STUB)
        (bindir / tool).chmod(0o755)
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "STUB_LOG": str(tmp_path / "stub.log"),
    }
    return subprocess.run(
        ["bash", str(root / "infra" / "deploy.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=root,
    )


def _stub_calls(tmp_path: Path) -> str:
    log = tmp_path / "stub.log"
    return log.read_text() if log.exists() else ""


def test_a_missing_infra_env_is_named(tmp_path: Path) -> None:
    root = _checkout(tmp_path, infra_env=False, root_env=True)
    result = _deploy(tmp_path, root, "orchestrator")
    assert result.returncode == 1, result
    missing = str(root / "infra" / ".env")
    assert missing in result.stderr, f"deploy.sh must name the missing {missing}, stderr was {result.stderr!r}"
    assert "docker" not in _stub_calls(tmp_path), "deploy.sh built or recreated without infra/.env"


def test_a_missing_root_env_is_named(tmp_path: Path) -> None:
    root = _checkout(tmp_path, infra_env=True, root_env=False)
    result = _deploy(tmp_path, root, "orchestrator")
    assert result.returncode == 1, result
    missing = str(root / ".env")
    assert f"{missing} is missing" in result.stderr, (
        f"deploy.sh must name the missing {missing}, stderr was {result.stderr!r}"
    )
    assert "docker" not in _stub_calls(tmp_path), "deploy.sh built or recreated without the root .env"


def test_with_both_env_files_the_deploy_proceeds(tmp_path: Path) -> None:
    """The guard must not stop a checkout that has both files."""
    root = _checkout(tmp_path, infra_env=True, root_env=True)
    result = _deploy(tmp_path, root, "orchestrator")
    assert result.returncode == 0, result
    assert "docker compose" in _stub_calls(tmp_path)
    assert ">> healthy" in result.stdout


def test_the_marker_without_a_root_env_says_why_nothing_was_pushed(tmp_path: Path) -> None:
    """`marker` needs only a Loki URL; a missing root .env means none, said out loud."""
    root = _checkout(tmp_path, infra_env=False, root_env=False)
    result = _deploy(tmp_path, root, "marker", "65340420f0a4", "orchestrator")
    assert result.returncode == 0, f"deploy.sh marker died without a root .env: {result}"
    assert "no Loki URL" in result.stderr, result.stderr
    assert "curl" not in _stub_calls(tmp_path)
