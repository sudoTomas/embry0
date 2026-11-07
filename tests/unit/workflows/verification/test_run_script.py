"""The run script itself, run by a real shell: what reaches the exec's streams, its status and the log.

``_run_once`` builds the run's exec; the sandbox here runs that exact argv on
this host, with only the log directory and the directory to run in moved into
``tmp_path``, and reads the log back with the gate's own ``tail`` exec. The
process sweep is replaced by a clean result for the whole module: its script
is only ever run against a process table a test writes (``test_sweep.py``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from embry0.workflows.verification import runner
from embry0.workflows.verification.config import TestTarget

pytestmark = pytest.mark.skipif(
    not (shutil.which("sh") and shutil.which("bash") and shutil.which("timeout") and shutil.which("tail")),
    reason="needs sh, bash, timeout and tail",
)

#: A locale no image has: bash warns on stderr when it starts under it.
MISSING_LOCALE = "en_ZZ.UTF-8"


@pytest.fixture(autouse=True)
def _no_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    async def clean(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(clean=True, describe=lambda: "the sandbox was cleared")

    monkeypatch.setattr(runner, "sweep_sandbox", clean)


class _LocalSandbox:
    """Runs the run's exec and its log read on this host. Records what each printed and returned."""

    def __init__(self, tmp: Path, *, env: dict[str, str] | None = None, run_dir: Path | None = None) -> None:
        self.logs = tmp / "logs"
        self.logs.mkdir()
        self.work = tmp / "work"
        self.work.mkdir()
        self.run_dir = run_dir or self.work
        self.env = env or {}
        self.runs: list[tuple[list[str], int, str, str]] = []

    def build_exec_cmd(self, container: str, command: list[str], workdir: Any = None, env: Any = None, **_: Any):
        return {"command": list(command), "env": dict(env or {})}

    async def run_cmd_status(self, cmd: dict[str, Any], timeout: int = 60, *, max_bytes: Any = None):
        command, env = cmd["command"], cmd["env"]
        assert not any("kill" in word for word in command), f"only the run and its log read run here: {command[:2]}"
        is_run = "EMBRY0_VERIFY_CMD" in env
        assert is_run or command[0] == "tail", f"unexpected exec: {command[:2]}"
        argv = [word.replace(runner.LOG_DIR, str(self.logs)) for word in command]
        full = {"PATH": os.environ["PATH"], **self.env, **env}
        if is_run:
            full["EMBRY0_VERIFY_DIR"] = str(self.run_dir)
        done = subprocess.run(argv, env=full, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        out, err = done.stdout.decode(errors="replace"), done.stderr.decode(errors="replace")
        if is_run:
            self.runs.append((argv, done.returncode, out, err))
        return done.returncode, out, err

    def log(self, name: str) -> str:
        path = self.logs / name
        return path.read_text(errors="replace") if path.exists() else ""


def _target(command: str, timeout_seconds: int = 60) -> TestTarget:
    # model_construct: a one-second timeout is below what a stored config may ask for.
    return TestTarget.model_construct(name="unit", command=command, paths=[], timeout_seconds=timeout_seconds)


async def _run(sandbox: _LocalSandbox, command: str, *, timeout_seconds: int = 60) -> Any:
    return await runner._run_once(sandbox, "local", _target(command, timeout_seconds), "head", 1)


@pytest.mark.parametrize(
    ("code", "outcome"),
    [(0, "passed"), (1, "failed"), (124, "timed_out"), (137, "timed_out"), (143, "failed"), (255, "failed")],
)
async def test_the_commands_exit_status_comes_through_unchanged(tmp_path: Path, code: int, outcome: str) -> None:
    sandbox = _LocalSandbox(tmp_path)
    run = await _run(sandbox, f"echo 'ran {code}'; echo 'to stderr' >&2; exit {code}")
    ((_, status, out, err),) = sandbox.runs
    assert status == code, f"the exec exits with the command's own status: {status}, not {code}"
    assert (out, err) == ("", ""), f"the run script prints nothing on the exec's streams: {out!r} {err!r}"
    assert sandbox.log("unit.head.1.log") == f"ran {code}\nto stderr\n", "the command's output goes to the log"
    assert (run.outcome, run.exit_code) == (outcome, code), run


async def test_a_command_that_is_not_found_fails_with_127_and_says_so_in_the_log(tmp_path: Path) -> None:
    sandbox = _LocalSandbox(tmp_path)
    run = await _run(sandbox, "embry0-no-such-command-anywhere")
    ((_, status, out, err),) = sandbox.runs
    assert (status, out, err) == (127, "", ""), (status, out, err)
    assert "embry0-no-such-command-anywhere" in sandbox.log("unit.head.1.log")
    assert (run.outcome, run.exit_code) == ("failed", 127), run


async def test_the_scripts_own_timeout_stops_the_command_with_124(tmp_path: Path) -> None:
    sandbox = _LocalSandbox(tmp_path)
    run = await _run(sandbox, "echo started; sleep 30", timeout_seconds=1)
    ((_, status, out, err),) = sandbox.runs
    assert (status, out, err) == (124, "", ""), (status, out, err)
    assert sandbox.log("unit.head.1.log") == "started\n"
    assert run.outcome == "timed_out", run


async def test_a_run_directory_that_does_not_exist_prints_the_fixed_line_alone(tmp_path: Path) -> None:
    sandbox = _LocalSandbox(tmp_path, run_dir=tmp_path / "missing")
    run = await _run(sandbox, "echo should-not-run")
    ((_, status, out, err),) = sandbox.runs
    assert (out, err) == (f"{runner._NO_RUN_DIR}\n", ""), f"only the fixed line, on stdout: {out!r} {err!r}"
    assert status != 0
    assert sandbox.log("unit.head.1.log") == "", "nothing ran"
    assert (run.outcome, run.output_tail) == ("errored", "the directory to run in could not be entered in the sandbox")


async def test_a_locale_the_image_lacks_leaves_the_execs_streams_empty(tmp_path: Path) -> None:
    """A repo or profile can set LC_ALL to a locale the image does not have; the run must still read green."""
    probe = {"PATH": os.environ["PATH"], "LC_ALL": MISSING_LOCALE}
    warns = subprocess.run(["bash", "-c", "true"], env=probe, capture_output=True).stderr
    assert warns, f"bash on this host does not warn under {MISSING_LOCALE}: the case below would measure nothing"
    if subprocess.run(["sh", "-c", "true"], env=probe, capture_output=True).stderr:
        pytest.skip("this host's sh warns at startup under a missing locale; the sandbox images' sh (dash) does not")

    sandbox = _LocalSandbox(tmp_path, env={"LC_ALL": MISSING_LOCALE})
    run = await _run(sandbox, "echo '1 passed'")
    ((_, status, out, err),) = sandbox.runs
    assert (status, out, err) == (0, "", ""), (
        f"the exec's streams must stay empty under LC_ALL={MISSING_LOCALE}: {err!r}"
    )
    assert "1 passed" in sandbox.log("unit.head.1.log")
    assert run.outcome == "passed", f"{run.outcome}: {run.output_tail!r}"
