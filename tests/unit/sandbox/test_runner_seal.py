"""The sandbox runner is closed to other processes of its user, and writes the run's mark on its result only.

Spec 2026-11-23, an agent run's own result. The code a job runs is of the
sandbox's user; a process of the same user may open another's
``/proc/<pid>/fd`` (and write on its stdout) unless that process is not
dumpable. The runner seals itself first thing, before its imports take their
time; in the image it starts on an interpreter its user may run but not read,
which the kernel starts sealed (checked in the image's CI smoke, not here).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from embry0.sandbox import runner

_TREE = Path(__file__).resolve().parents[3]
_LINUX = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the dumpable flag is Linux's")
_NOT_ROOT = pytest.mark.skipif(os.geteuid() == 0, reason="root opens any process: nothing to measure as root")
#: How long the test waits for the runner to seal itself; it does so before its imports, well within this.
_SEAL_WITHIN_SECONDS = 15.0
_GET_DUMPABLE = "import ctypes; print(ctypes.CDLL(None).prctl(3, 0, 0, 0, 0))"


def _python(code: str) -> str:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(_TREE)}
    out = subprocess.run(
        [sys.executable, "-P", "-s", "-c", code], env=env, capture_output=True, text=True, timeout=60, check=False
    )
    return out.stdout.strip() + out.stderr.strip()[-400:]


@_LINUX
@_NOT_ROOT
def test_another_process_of_the_user_cannot_open_the_running_runner() -> None:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(_TREE)}
    # Its stdin stays open, so the runner waits for its config: alive, and past the point it seals itself.
    proc = subprocess.Popen(
        [sys.executable, "-P", "-s", "-m", "embry0.sandbox.runner", "--config-stdin"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    opened: dict[str, str] = {}
    try:
        deadline = time.monotonic() + _SEAL_WITHIN_SECONDS
        while time.monotonic() < deadline and proc.poll() is None:
            opened = {}
            for name, flags in (("fd/1", os.O_WRONLY), ("fd/0", os.O_RDONLY), ("mem", os.O_RDONLY)):
                try:
                    os.close(os.open(f"/proc/{proc.pid}/{name}", flags))
                    opened[name] = "opened"
                except PermissionError:
                    pass
            if not opened:
                break
            time.sleep(0.05)
        assert proc.poll() is None, f"the runner exited before it could be checked: {proc.returncode}"
    finally:
        proc.kill()
        proc.communicate(timeout=30)

    assert opened == {}, (
        f"another process of the runner's user still opens it {_SEAL_WITHIN_SECONDS:.0f}s after it started: {opened}"
    )


@_LINUX
def test_seal_says_whether_the_process_was_open_until_then() -> None:
    out = _python(
        "import ctypes\n"
        "from embry0.sandbox import runner\n"
        "seal = getattr(runner, 'seal', None)\n"
        "print(seal and seal(), seal and seal(), ctypes.CDLL(None).prctl(3, 0, 0, 0, 0))\n"
    )

    assert out == "True False 0", (
        f"seal() on an open process, again, then the flag: expected 'True False 0', got {out!r}"
    )


@_LINUX
def test_what_the_sealed_runner_starts_is_its_own_again() -> None:
    """The CLI and its tools are processes the runner starts: an exec opens them again, so they work as before."""
    out = _python(
        "import subprocess, sys\n"
        "from embry0.sandbox import runner\n"
        "getattr(runner, 'seal', lambda: None)()\n"
        f"print(subprocess.run([sys.executable, '-c', {_GET_DUMPABLE!r}], capture_output=True, text=True).stdout)\n"
    )

    assert out == "1", f"a program the sealed runner starts is not dumpable: {out!r}"


def _main(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], config: dict[str, Any]) -> list[Any]:
    """Run the runner's main() on ``config`` with a stub agent run; its stdout lines and the agent run's kwargs."""
    seen: list[dict[str, Any]] = []

    async def _run_agent(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs)
        runner._emit({"type": "agent_started", "model": "custom/m", "max_turns": 5})
        return {"agent_type": "developer", "is_error": False, "output": "done"}

    monkeypatch.setattr(runner, "run_agent", _run_agent)
    monkeypatch.setattr(sys, "argv", ["runner", "--config-stdin", "--result-nonce"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(config)))
    with pytest.raises(SystemExit) as exited:
        runner.main()
    assert exited.value.code == 0, f"the runner refused its command line or failed: exit {exited.value.code}"
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    return [lines, seen]


def test_the_runner_writes_the_mark_on_its_final_result_and_nowhere_else(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    nonce = "zz-the-run-s-mark-0123456789abcdef"
    lines, seen = _main(monkeypatch, capsys, {"agent_type": "developer", "prompt": "fix it", "result_nonce": nonce})

    assert lines[-1]["type"] == "final_result" and lines[-1].get("nonce") == nonce, (
        f"the final result does not carry the run's mark: {lines[-1]}"
    )
    assert not any(nonce in json.dumps(line) for line in lines[:-1]), "the mark is on a line before the result"
    assert nonce not in repr(seen), "the mark reached the agent's invocation"


@pytest.mark.parametrize("open_at_start", [True, False])
def test_the_runner_reports_whether_it_started_open(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], open_at_start: bool
) -> None:
    monkeypatch.setattr(runner, "OPEN_AT_START", open_at_start, raising=False)
    lines, _ = _main(monkeypatch, capsys, {"agent_type": "developer", "prompt": "fix it", "result_nonce": "zz"})

    assert lines[-1].get("open_at_start") is open_at_start, f"its result does not say so: {lines[-1]}"
