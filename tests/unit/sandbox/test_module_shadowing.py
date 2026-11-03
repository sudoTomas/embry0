"""Code the job ran never loads into embry0's own Python processes in the sandbox.

The sandbox runner, the log tool's MCP server and the QA ready probe run as
the agent user in /workspace, where the job's repository is checked out. Two
places that code can write would otherwise load first:

- the working directory: ``python -m`` and ``python -c`` put it first on
  sys.path, ahead of the image's /app (PYTHONPATH), so a checkout's own
  ``embry0/`` package, or a ``urllib/``, would run instead. ``-P`` keeps it off;
- the agent's user site-packages: Python runs its ``.pth`` files and
  ``usercustomize.py`` at start-up, and a ``pip install --user`` writes there.
  ``-s`` keeps it off.

Each test runs the exact command line embry0 uses, with a local interpreter
in place of ``python``/``python3``, next to a planted module, and requires that
the planted code does not run. The control tests run the same plants without
the flags and require that they do, so a check here can fail.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.execution.agent_runner import AgentRunner
from embry0.workflows.issue_to_pr.log_tool import CONTEXT_MCP_SERVER

_TREE = Path(__file__).resolve().parents[3]
_UNREACHABLE = "http://127.0.0.1:9/"  # the discard port: nothing answers


def _plant_package(workspace: Path, marker: Path, *modules: str) -> None:
    """A checkout holding ``embry0/sandbox/<module>.py`` files that write ``marker`` when they run."""
    package = workspace / "embry0" / "sandbox"
    package.mkdir(parents=True, exist_ok=True)
    (workspace / "embry0" / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    for module in modules:
        (package / f"{module}.py").write_text(f"open({str(marker)!r}, 'w').write('ran')\n")


def _plant_urllib(workspace: Path) -> None:
    """A checkout holding a ``urllib/`` whose ``urlopen`` says every URL is up."""
    package = workspace / "urllib"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "error.py").write_text("class HTTPError(Exception):\n    pass\n")
    (package / "request.py").write_text(
        "class Request:\n"
        "    def __init__(self, url, headers=None):\n"
        "        self.url = url\n"
        "class _Up:\n"
        "    status = 200\n"
        "    def read(self, n):\n"
        "        return b'planted'\n"
        "def urlopen(req, timeout=None):\n"
        "    return _Up()\n"
    )


def _plant_user_site(userbase: Path, python: str, marker_dir: Path) -> None:
    """A ``.pth`` file and a ``usercustomize.py`` in the user site-packages of ``python`` under ``userbase``."""
    site_dir = subprocess.run(
        [python, "-c", "import site; print(site.getusersitepackages())"],
        env={"PYTHONUSERBASE": str(userbase)},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    target = Path(site_dir)
    target.mkdir(parents=True, exist_ok=True)
    (target / "zz-planted.pth").write_text(f"import os; open({str(marker_dir / 'pth-ran')!r}, 'w').write('ran')\n")
    (target / "usercustomize.py").write_text(f"open({str(marker_dir / 'usercustomize-ran')!r}, 'w').write('ran')\n")


def _run(argv: list[str], workspace: Path, *, python: str = sys.executable, userbase: Path | None = None) -> str:
    """``argv`` as the sandbox runs it, with ``python`` for ``python``/``python3``; returns stdout."""
    command = [python if argv[0] in ("python", "python3") else argv[0], *argv[1:]]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(_TREE)}
    if userbase is not None:
        env["PYTHONUSERBASE"] = str(userbase)
    out = subprocess.run(
        command, cwd=workspace, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60
    )
    return out.stdout


async def _runner_command() -> list[str]:
    """The command AgentRunner execs in the sandbox."""
    seen: list[list[str]] = []
    proc = MagicMock()
    proc.stdout.__aiter__.return_value = iter(())
    proc.stderr.read = AsyncMock(return_value=b"")
    proc.wait = AsyncMock(return_value=0)
    proc.returncode = 0

    async def stream_exec(*, container: str, command: list[str], **_kwargs: object) -> MagicMock:
        seen.append(list(command))
        return proc

    docker = AsyncMock()
    docker.stream_exec = AsyncMock(side_effect=stream_exec)
    await AgentRunner(sandbox_manager=AsyncMock(), docker=docker).run("c", {"agent_type": "x", "timeout_seconds": 5})
    assert seen, "AgentRunner never ran a command"
    return seen[0]


def _context_command() -> list[str]:
    server = CONTEXT_MCP_SERVER["context"]
    return [server["command"], *server["args"]]


async def _probe_command() -> list[str]:
    """The command the QA ready check runs in the sandbox, for an unreachable URL."""
    from embry0.workflows.qa.boot import _probe_ready_check

    seen: list[list[str]] = []
    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(side_effect=lambda _container, command, **_kw: list(command))

    async def run_cmd(command: list[str], **_kw: Any) -> str:
        seen.append(command)
        return "STATUS=0\n---\n"

    docker.run_cmd = AsyncMock(side_effect=run_cmd)
    await _probe_ready_check(docker=docker, container_id="c", url=_UNREACHABLE)
    assert seen, "the ready check ran no command"
    return seen[0]


@pytest.fixture
def base_python(tmp_path: Path) -> str:
    """An interpreter outside any virtualenv, whose user site-packages is enabled, as in the sandbox images."""
    python = getattr(sys, "_base_executable", "") or ""
    if not python or not os.access(python, os.X_OK):
        pytest.skip("no base interpreter outside the virtualenv")
    enabled = subprocess.run(
        [python, "-c", "import site; print(site.ENABLE_USER_SITE)"],
        env={"PYTHONUSERBASE": str(tmp_path / "probe")},
        capture_output=True,
        text=True,
    ).stdout.strip()
    if enabled != "True":
        pytest.skip(f"{python} has no user site-packages here")
    return python


# ---- the working directory ---------------------------------------------------------


async def test_the_sandbox_runner_runs_the_images_module_not_the_checkouts(tmp_path: Path) -> None:
    workspace, marker = tmp_path / "workspace", tmp_path / "planted-ran"
    _plant_package(workspace, marker, "runner")
    command = await _runner_command()
    assert "embry0.sandbox.runner" in command, command
    _run(command, workspace)
    assert not marker.exists(), f"the checkout's embry0/sandbox/runner.py ran in place of the runner: {command}"


def test_the_log_tools_server_runs_the_images_module_not_the_checkouts(tmp_path: Path) -> None:
    workspace, marker = tmp_path / "workspace", tmp_path / "planted-ran"
    _plant_package(workspace, marker, "context_mcp")
    command = _context_command()
    _run(command, workspace)
    assert not marker.exists(), f"the checkout's embry0/sandbox/context_mcp.py ran in place of the server: {command}"


async def test_a_checkouts_urllib_cannot_answer_the_qa_ready_check(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _plant_urllib(workspace)
    command = await _probe_command()
    out = _run(command, workspace)
    assert out.startswith("STATUS=0"), f"the checkout's urllib answered the ready check ({command[:4]}): {out[:80]!r}"


@pytest.mark.parametrize("module", ["runner", "context_mcp"])
def test_plain_dash_m_would_run_the_checkouts_module(tmp_path: Path, module: str) -> None:
    """Why -P is needed, and proof that the checks above can see a planted module run."""
    workspace, marker = tmp_path / "workspace", tmp_path / "planted-ran"
    _plant_package(workspace, marker, module)
    _run(["python3", "-m", f"embry0.sandbox.{module}"], workspace)
    assert marker.exists(), "plain -m no longer puts the working directory first; revisit these tests"


async def test_plain_dash_c_would_let_a_checkouts_urllib_answer(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _plant_urllib(workspace)
    command = await _probe_command()
    out = _run(["python3", "-c", *command[-2:]], workspace)
    assert out.startswith("STATUS=200"), f"plain -c no longer puts the working directory first: {out[:80]!r}"


# ---- the user site-packages --------------------------------------------------------


async def _commands() -> dict[str, list[str]]:
    return {"runner": await _runner_command(), "log server": _context_command(), "ready check": await _probe_command()}


@pytest.mark.parametrize("which", ["runner", "log server", "ready check"])
async def test_the_user_site_packages_never_load(tmp_path: Path, base_python: str, which: str) -> None:
    workspace, userbase = tmp_path / "workspace", tmp_path / "userbase"
    workspace.mkdir()
    _plant_user_site(userbase, base_python, tmp_path)
    command = (await _commands())[which]
    _run(command, workspace, python=base_python, userbase=userbase)
    ran = sorted(p.name for p in tmp_path.glob("*-ran"))
    assert not ran, f"the {which} ({command[:5]}) loaded the user site-packages: {ran}"


async def test_without_dash_s_the_user_site_packages_load(tmp_path: Path, base_python: str) -> None:
    """Why -s is needed, and proof that the check above can see the user site load."""
    workspace, userbase = tmp_path / "workspace", tmp_path / "userbase"
    workspace.mkdir()
    _plant_user_site(userbase, base_python, tmp_path)
    _run(["python", "-P", "-m", "embry0.sandbox.runner"], workspace, python=base_python, userbase=userbase)
    ran = sorted(p.name for p in tmp_path.glob("*-ran"))
    assert ran == ["pth-ran", "usercustomize-ran"], f"-P alone no longer loads the user site-packages: {ran}"
