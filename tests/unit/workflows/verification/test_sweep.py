"""The process sweep around every gate run: what it stops, what it keeps, and that the script and the function agree.

The selection rules are tested as a function of a process table. The script is
then run, with real bash, against a process table this test writes under
``tmp_path`` (``render_script(proc_root=...)``) and a ``signal`` command that
only records the PID and marks the process exited (a zombie, which the sweep
ignores). It runs with ``PATH=/nonexistent`` and a ``command_not_found_handle``
that records any attempt to start a program: the sweep uses bash builtins
only. No signal is ever sent to a real process.
"""

from __future__ import annotations

import importlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="needs bash")

UID = os.geteuid()
OTHER_UID = UID + 1
# Above the kernel's largest PID (4194304), so no PID here can name a real process.
BASE_PID = 5_000_000


def _sweep() -> Any:
    try:
        return importlib.import_module("embry0.workflows.verification.sweep")
    except ModuleNotFoundError:
        pytest.fail("embry0.workflows.verification.sweep is missing: nothing sweeps the sandbox around a run")


def _table(*rows: tuple[int, int, str, int | None]) -> list[Any]:
    sweep = _sweep()
    return [sweep.Process(pid=pid, uid=uid, state=state, start=start) for pid, uid, state, start in rows]


# ── the selection, as a function ─────────────────────────────────────────────

INIT = (1, UID, "S", 100)
COMMAND = (BASE_PID + 7, UID, "S", 101)  # `sleep infinity`, the oldest after PID 1
SELF = BASE_PID + 900


def test_pid_1_and_the_containers_command_are_kept() -> None:
    table = _table(INIT, COMMAND, (BASE_PID + 50, UID, "S", 500), (SELF, UID, "R", 900))
    stop = _sweep().select_to_stop(table, uid=UID, self_pid=SELF)
    assert 1 not in stop, f"PID 1 (the container's init) must never be stopped: {stop}"
    assert COMMAND[0] not in stop, f"the oldest process after PID 1 is the container's command and must stay: {stop}"
    assert stop == [BASE_PID + 50], f"every other process of the user is stopped: {stop}"


def test_the_sweeps_own_process_is_kept() -> None:
    table = _table(INIT, COMMAND, (SELF, UID, "R", 900))
    assert _sweep().select_to_stop(table, uid=UID, self_pid=SELF) == [], "the sweep must not stop itself"


def test_the_users_other_processes_are_selected_and_other_users_are_not() -> None:
    table = _table(
        INIT,
        COMMAND,
        (BASE_PID + 50, UID, "S", 500),
        (BASE_PID + 51, UID, "R", 501),
        (BASE_PID + 52, UID, "D", 502),
        (BASE_PID + 53, UID, "T", 503),
        (BASE_PID + 60, OTHER_UID, "S", 600),
        (SELF, UID, "R", 900),
    )
    stop = _sweep().select_to_stop(table, uid=UID, self_pid=SELF)
    assert stop == [BASE_PID + 50, BASE_PID + 51, BASE_PID + 52, BASE_PID + 53], (
        f"every live process of the user, running, sleeping, in I/O or stopped, is selected, and no one else's: {stop}"
    )


def test_zombies_are_ignored() -> None:
    table = _table(INIT, COMMAND, (BASE_PID + 50, UID, "Z", 500), (BASE_PID + 51, UID, "X", 501), (SELF, UID, "R", 900))
    stop = _sweep().select_to_stop(table, uid=UID, self_pid=SELF)
    assert stop == [], f"a zombie has already exited; signalling it does nothing: {stop}"


def test_a_tie_on_start_time_keeps_the_lower_pid() -> None:
    table = _table(INIT, (BASE_PID + 8, UID, "S", 101), (BASE_PID + 7, UID, "S", 101), (SELF, UID, "R", 900))
    stop = _sweep().select_to_stop(table, uid=UID, self_pid=SELF)
    assert stop == [BASE_PID + 8], f"of two processes started in the same tick, the lower PID is the command: {stop}"


def test_the_command_is_the_oldest_whoever_owns_it() -> None:
    table = _table(INIT, (BASE_PID + 7, OTHER_UID, "S", 101), (BASE_PID + 8, UID, "S", 102), (SELF, UID, "R", 900))
    stop = _sweep().select_to_stop(table, uid=UID, self_pid=SELF)
    assert stop == [BASE_PID + 8], f"only the oldest process is kept as the command, not the user's oldest: {stop}"


def test_a_process_whose_start_cannot_be_read_is_never_kept_as_the_command() -> None:
    table = _table(INIT, (BASE_PID + 2, UID, "S", None), COMMAND, (SELF, UID, "R", 900))
    stop = _sweep().select_to_stop(table, uid=UID, self_pid=SELF)
    assert stop == [BASE_PID + 2], f"a process with no readable start time cannot be the container's command: {stop}"


# ── the script, run against a process table of its own ───────────────────────

_HARNESS = r"""
command_not_found_handle() { printf '%s\n' "$1" >> "$EMBRY0_TEST_CTL/started"; return 127; }
embry0_test_signal() {
  local p=$1 c line
  printf '%s\n' "$p" >> "$EMBRY0_TEST_CTL/signalled"
  [ -e "$EMBRY0_TEST_CTL/$p.immortal" ] && return 0
  read -r -d '' line < "$EMBRY0_TEST_CTL/$p.dead"
  printf '%s' "$line" > "$EMBRY0_TEST_ROOT/$p/status"
  if [ -e "$EMBRY0_TEST_CTL/$p.spawn" ]; then
    read -r c < "$EMBRY0_TEST_CTL/$p.spawn"
    read -r -d '' line < "$EMBRY0_TEST_CTL/$c.stat"
    printf '%s' "$line" > "$EMBRY0_TEST_ROOT/$c/stat"
    read -r -d '' line < "$EMBRY0_TEST_CTL/$c.status"
    printf '%s' "$line" > "$EMBRY0_TEST_ROOT/$c/status"
  fi
}
read -r _
eval "$EMBRY0_TEST_SCRIPT"
"""


def _stat(pid: int, comm: str, state: str, start: int | None) -> str:
    # Every other field is far larger than any start time here: a field read
    # from the wrong position is never mistaken for the oldest start.
    fields = [state, *["99999999"] * 18, "" if start is None else str(start), *["99999999"] * 30]
    if start is None:
        fields = fields[:19]  # the line ends before the start time
    return f"{pid} ({comm}) {' '.join(fields)}\n"


def _status(pid: int, uid: int, state: str) -> str:
    return (
        f"Name:\tproc{pid}\nUmask:\t0022\nState:\t{state} (state)\nTgid:\t{pid}\nPid:\t{pid}\nPPid:\t1\n"
        f"Uid:\t{uid}\t{uid}\t{uid}\t{uid}\nGid:\t{uid}\t{uid}\t{uid}\t{uid}\n"
    )


@dataclass
class _Row:
    pid: int
    uid: int
    state: str
    start: int | None
    comm: str = "proc"


@dataclass
class _Ran:
    signalled: list[int]
    started: list[str]
    report: Any
    stdout: str
    self_pid: int
    table: list[Any] = field(default_factory=list)


def _write(root: Path, ctl: Path, row: _Row, *, visible: bool = True) -> None:
    d = root / str(row.pid)
    d.mkdir(parents=True, exist_ok=True)
    stat, status = _stat(row.pid, row.comm, row.state, row.start), _status(row.pid, row.uid, row.state)
    (ctl / f"{row.pid}.dead").write_text(_status(row.pid, row.uid, "Z"))
    if visible:
        (d / "stat").write_text(stat)
        (d / "status").write_text(status)
    else:
        (ctl / f"{row.pid}.stat").write_text(stat)
        (ctl / f"{row.pid}.status").write_text(status)


def _run(
    tmp_path: Path,
    rows: list[_Row],
    *,
    spawns: dict[int, _Row] | None = None,
    immortal: tuple[int, ...] = (),
    max_passes: int = 5,
) -> _Ran:
    """Run the rendered sweep on ``rows`` (plus its own process, written once bash has a PID)."""
    sweep = _sweep()
    root, ctl = tmp_path / "proc", tmp_path / "ctl"
    root.mkdir()
    ctl.mkdir()
    for row in rows:
        _write(root, ctl, row)
    for parent, child in (spawns or {}).items():
        _write(root, ctl, child, visible=False)
        (ctl / f"{parent}.spawn").write_text(f"{child.pid}\n")
    for pid in immortal:
        (ctl / f"{pid}.immortal").write_text("")
    script = sweep.render_script(
        proc_root=str(root), signal="embry0_test_signal", max_passes=max_passes, pass_wait=0.01
    )
    assert BASH is not None
    env = {"PATH": "/nonexistent", "EMBRY0_TEST_ROOT": str(root), "EMBRY0_TEST_CTL": str(ctl)}
    env["EMBRY0_TEST_SCRIPT"] = script
    proc = subprocess.Popen(
        [BASH, "-c", _HARNESS], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    )
    me = _Row(proc.pid, UID, "R", 900, comm="bash")
    _write(root, ctl, me)
    out, err = proc.communicate(b"\n", timeout=30)
    assert proc.returncode == 0, f"the sweep script failed: {err.decode()!r}"
    signalled_file, started_file = ctl / "signalled", ctl / "started"
    signalled = [int(p) for p in signalled_file.read_text().split()] if signalled_file.exists() else []
    started = started_file.read_text().split() if started_file.exists() else []
    report = sweep.read_report(proc.returncode, out.decode(), err.decode())
    table = [sweep.Process(r.pid, r.uid, r.state, r.start) for r in [*rows, me]]
    return _Ran(signalled, started, report, out.decode(), proc.pid, table)


_TABLES = {
    "a-sandbox-with-leftovers": [
        _Row(*INIT),
        _Row(*COMMAND, comm="sleep"),
        _Row(BASE_PID + 50, UID, "S", 500, comm="node"),
        _Row(BASE_PID + 51, UID, "R", 501, comm="java"),
        _Row(BASE_PID + 60, OTHER_UID, "S", 600),
        _Row(BASE_PID + 70, UID, "Z", 700),
    ],
    "a-tie-on-start": [
        _Row(*INIT),
        _Row(BASE_PID + 8, UID, "S", 101),
        _Row(BASE_PID + 7, UID, "S", 101),
        _Row(BASE_PID + 9, UID, "S", 400),
    ],
    # The command's own name holds a newline and a ") ": its start time,
    # which decides what is kept, is read past both.
    "names-that-look-like-fields": [
        _Row(*INIT),
        _Row(*COMMAND, comm="sl\neep) S 1 2"),
        _Row(BASE_PID + 50, UID, "S", 500, comm="x) Z 0 0 0"),
        _Row(BASE_PID + 51, UID, "S", 501, comm="a\nb) S"),
    ],
    "an-unreadable-start-time": [
        _Row(*INIT),
        _Row(BASE_PID + 2, UID, "S", None),
        _Row(*COMMAND),
    ],
    "nothing-left": [_Row(*INIT), _Row(*COMMAND)],
}


@needs_bash
@pytest.mark.parametrize("name", sorted(_TABLES))
def test_the_script_stops_exactly_what_the_function_selects(tmp_path: Path, name: str) -> None:
    ran = _run(tmp_path, _TABLES[name])
    expected = _sweep().select_to_stop(ran.table, uid=UID, self_pid=ran.self_pid)
    assert sorted(ran.signalled) == expected, f"{name}: the script stopped {ran.signalled}, the rules say {expected}"
    assert ran.report.clean, f"{name}: every selected process exited, so the sweep is clean: {ran.stdout!r}"
    assert ran.report.stopped == len(expected), f"{name}: the report counts what it stopped: {ran.stdout!r}"
    assert ran.report.passes == (1 if expected else 0), f"{name}: one pass stops all, then one finds nothing"
    assert ran.started == [], f"{name}: the sweep started a program, not bash builtins only: {ran.started}"


@needs_bash
def test_a_process_that_appears_between_passes_is_caught_by_a_later_pass(tmp_path: Path) -> None:
    rows = [_Row(*INIT), _Row(*COMMAND), _Row(BASE_PID + 50, UID, "S", 500)]
    child = _Row(BASE_PID + 51, UID, "S", 950, comm="respawned")
    ran = _run(tmp_path, rows, spawns={BASE_PID + 50: child})
    assert ran.signalled == [BASE_PID + 50, BASE_PID + 51], (
        f"the process started while the first pass ran must be stopped by the next one: {ran.signalled}"
    )
    assert ran.report.clean and ran.report.passes == 2 and ran.report.stopped == 2, ran.stdout
    assert ran.started == []


@needs_bash
def test_the_pass_bound_is_respected_and_what_survives_is_reported(tmp_path: Path) -> None:
    stuck = BASE_PID + 50
    rows = [_Row(*INIT), _Row(*COMMAND), _Row(stuck, UID, "D", 500)]
    ran = _run(tmp_path, rows, immortal=(stuck,), max_passes=3)
    assert ran.signalled == [stuck] * 3, (
        f"a process that does not exit is signalled once per pass, three passes: {ran.signalled}"
    )
    report = ran.report
    assert not report.clean, f"a process left running makes the sweep unclean: {ran.stdout!r}"
    assert (report.passes, report.survivors, report.pids) == (3, 1, (stuck,)), ran.stdout
    assert str(stuck) in report.describe(), report.describe()


@needs_bash
def test_a_respawning_process_cannot_outlast_the_bound(tmp_path: Path) -> None:
    rows = [_Row(*INIT), _Row(*COMMAND), _Row(BASE_PID + 50, UID, "S", 500)]
    chain = {BASE_PID + 50 + n: _Row(BASE_PID + 51 + n, UID, "S", 950 + n) for n in range(5)}
    ran = _run(tmp_path, rows, spawns=chain, max_passes=3)
    assert ran.signalled == [BASE_PID + 50, BASE_PID + 51, BASE_PID + 52], ran.signalled
    assert not ran.report.clean and ran.report.pids == (BASE_PID + 53,), ran.stdout


def test_production_runs_the_kill_builtin_with_the_default_bounds() -> None:
    sweep = _sweep()
    assert sweep.render_script() == sweep.SCRIPT
    assert 'kill -KILL "$p"' in sweep.SCRIPT, "production stops a process with the kill builtin"
    assert f'"$passes" -ge {sweep.MAX_PASSES}' in sweep.SCRIPT
    # $( ... ) and backticks start a subshell; $(( ... )) is arithmetic, done in bash itself.
    for pattern in (r"\$\((?!\()", "`", "/bin/", "/usr/"):
        assert not re.search(pattern, sweep.SCRIPT), f"the sweep must not start a process ({pattern!r} matched)"


# ── the report ───────────────────────────────────────────────────────────────


def test_a_clean_report_is_read() -> None:
    result = _sweep().read_report(0, "EMBRY0_SWEEP stopped=3 passes=2 survivors=0 pids=\n", "")
    assert result.clean and (result.stopped, result.passes, result.survivors, result.pids) == (3, 2, 0, ())


def test_a_report_naming_survivors_is_unclean() -> None:
    result = _sweep().read_report(0, "EMBRY0_SWEEP stopped=3 passes=20 survivors=2 pids=41,42\n", "")
    assert not result.clean and result.pids == (41, 42)
    assert result.describe() == "2 processes could not be stopped (pids 41, 42)", result.describe()


@pytest.mark.parametrize(
    ("code", "stdout", "stderr"),
    [
        (1, "EMBRY0_SWEEP stopped=0 passes=0 survivors=0 pids=\n", ""),
        (0, "EMBRY0_SWEEP stopped=0 passes=0 survivors=0 pids=\n", "Error response from daemon: container is paused\n"),
        (
            0,
            "EMBRY0_SWEEP stopped=0 passes=0 survivors=0 pids=\nEMBRY0_SWEEP stopped=0 passes=0 survivors=0 pids=\n",
            "",
        ),
        (0, "EMBRY0_SWEEP stopped=0 passes=0 survivors=0 pids=", ""),
        (0, "", ""),
        (137, "", ""),
        (0, "EMBRY0_SWEEP stopped=1 passes=1 survivors=1 pids=\n", ""),
        (0, "EMBRY0_SWEEP stopped=1 passes=1 survivors=0 pids=7\n", ""),
        (0, "EMBRY0_SWEEP stopped=1 passes=99 survivors=0 pids=\n", ""),
        (0, "x EMBRY0_SWEEP stopped=0 passes=0 survivors=0 pids=\n", ""),
    ],
    ids=[
        "non-zero-exit",
        "a-docker-message",
        "a-second-line",
        "no-newline",
        "nothing",
        "killed",
        "survivors-without-pids",
        "pids-without-survivors",
        "more-passes-than-the-bound",
        "a-prefix",
    ],
)
def test_a_report_that_is_not_exactly_one_well_formed_line_is_unclean(code: int, stdout: str, stderr: str) -> None:
    result = _sweep().read_report(code, stdout, stderr)
    assert not result.clean, f"only exit 0, an empty stderr and one well-formed line is a clean sweep: {stdout!r}"
    assert result.describe() == "the sandbox's processes could not be checked", result.describe()


class _Docker:
    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.calls: list[tuple[list[str], dict[str, Any], int]] = []

    def build_exec_cmd(self, container: str, command: list[str], **kwargs: Any) -> list[str]:
        self.calls.append((command, kwargs, 0))
        return ["docker", "exec", container, *command]

    async def run_cmd_status(self, cmd: list[str], timeout: int = 60, **_: Any) -> tuple[int, str, str]:
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


async def test_the_sweep_runs_the_script_as_the_containers_default_user() -> None:
    sweep = _sweep()
    docker = _Docker((0, "EMBRY0_SWEEP stopped=0 passes=0 survivors=0 pids=\n", ""))
    result = await sweep.sweep_sandbox(docker, "c1")
    assert result.clean
    ((command, kwargs, _),) = docker.calls
    assert command == ["bash", "-c", sweep.SCRIPT]
    assert "user" not in kwargs, f"the sweep runs as the container's default user, not another: {kwargs}"


async def test_a_sweep_whose_exec_fails_is_unclean() -> None:
    result = await _sweep().sweep_sandbox(_Docker(TimeoutError("docker command timed out after 60s")), "c1")
    assert not result.clean and result.describe() == "the sandbox's processes could not be checked"
