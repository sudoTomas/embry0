"""What one step leaves running in the job's sandbox is stopped before the next starts (#395).

The stop runs inside the sandbox and reads its process table from ``/proc``. Here it reads a table this test keeps:
the sandbox's init and keep-alive written as the sandbox would show them, and each leftover a real process this test
started in a process group of its own, linked to its real ``/proc`` entry (so it leaves the table when it ends). The
stop signals only processes and groups the table names, so it reaches only what this test started.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.execution.sandbox_processes import (
    ATTEMPTS,
    LEFTOVER_STOP,
    PASS_DEADLINE,
    PASS_PAUSE,
    LeftoversNotStopped,
    leftover_stop_command,
)

#: A process group that keeps coming back: its leader stays, and a member forks a successor and exits every 2 ms.
HOPPER = (
    "import os, time\n"
    "if os.fork() == 0:\n"
    "    while True:\n"
    "        time.sleep(0.002)\n"
    "        if os.fork():\n"
    "            os._exit(0)\n"
    "time.sleep(300)\n"
)


def _describe(proc: Path, pid: int, ppid: int, argv: list[str], *, group: int = 0) -> None:
    """``pid`` in the table as ``/proc`` shows it, written: its parent, its group and its command line."""
    (proc / str(pid)).mkdir(parents=True)
    (proc / str(pid) / "stat").write_bytes(f"{pid} (x) S {ppid} {group} {group} 0 -1".encode())
    (proc / str(pid) / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")


def _link(proc: Path, process: subprocess.Popen[bytes]) -> None:
    """A real process in the table, as the kernel shows it: it leaves the table when it ends."""
    proc.mkdir(parents=True, exist_ok=True)
    (proc / str(process.pid)).symlink_to(f"/proc/{process.pid}")


def _spawn(*argv: str) -> subprocess.Popen[bytes]:
    """A leftover: a real process in a process group of its own."""
    return subprocess.Popen(list(argv), start_new_session=True)


def _gone(process: subprocess.Popen[bytes], seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return True
        time.sleep(0.02)
    return False


def _group_gone(group: int, seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def started() -> Any:
    """The processes a test starts, each ended with its group after it, whatever happened."""
    procs: list[subprocess.Popen[bytes]] = []
    yield procs
    for process in procs:
        try:
            os.killpg(process.pid, 9)
        except (ProcessLookupError, PermissionError):
            pass
        if process.poll() is None:
            process.kill()
        process.wait()


#: The deadline the tests give a run of the stop that is not meant to end clean: long enough for dozens of passes.
QUICK = 0.5


def _script(deadline: float) -> str:
    """The stop, with its passes given ``deadline`` seconds in place of :data:`PASS_DEADLINE`."""
    full = f"deadline = time.monotonic() + {PASS_DEADLINE!r}"
    assert LEFTOVER_STOP.count(full) == 1, "the stop's deadline is not where the tests set it"
    return LEFTOVER_STOP.replace(full, f"deadline = time.monotonic() + {deadline!r}")


def _stop(proc_root: Path, deadline: float = QUICK) -> subprocess.CompletedProcess[str]:
    """One run of the stop over ``proc_root``, given ``deadline`` seconds for its passes."""
    return subprocess.run(
        [sys.executable, "-I", "-c", _script(deadline), str(proc_root)], capture_output=True, text=True, check=False
    )


def _quiet(proc_root: Path) -> None:
    """The sandbox's last process id, as ``/proc/sys/kernel/ns_last_pid`` shows it: no process is created while the
    stop reads this table."""
    (proc_root / "sys" / "kernel").mkdir(parents=True)
    (proc_root / "sys" / "kernel" / "ns_last_pid").write_bytes(b"4242\n")


def _stop_as_the_sandbox_runs_it(proc_root: Path, **run: Any) -> subprocess.CompletedProcess[str]:
    """The stop, run again while a run does not end clean, up to ``ATTEMPTS`` runs, as the sandbox manager runs it: on
    a busy host a process it signalled can take longer to end than one run's passes. The first clean run, else the
    last. How many a clean run says it stopped depends on how many runs it took, so the tests that use this check
    what is left, not that number (``test_a_process_signalled_on_more_than_one_pass_is_counted_once`` pins it)."""
    for _ in range(ATTEMPTS):
        done = subprocess.run(
            [sys.executable, "-I", "-c", LEFTOVER_STOP, str(proc_root)],
            capture_output=True,
            text=True,
            check=False,
            **run,
        )
        if done.returncode == 0:
            break
        assert done.returncode == 3, (done.returncode, done.stdout, done.stderr)
    return done


def _clean(done: subprocess.CompletedProcess[str], planted: int) -> bool:
    """The run ended clean, saying it stopped no more processes than the test planted."""
    said = done.stdout.strip()
    return done.returncode == 0 and said.isdigit() and int(said) <= planted


def _sandbox(proc_root: Path, started: Any) -> Path:
    """The init, and a keep-alive (a real process) the sandbox's init started."""
    _describe(proc_root, 1, 0, ["/sbin/docker-init", "--", "sleep", "infinity"])
    _quiet(proc_root)
    keepalive = _spawn("sleep", "300")
    started.append(keepalive)
    _describe(proc_root, keepalive.pid, 1, ["sleep", "infinity"], group=1)
    return proc_root


def test_the_stop_ends_what_steps_left_and_keeps_the_sandbox_alive(tmp_path: Path, started: Any) -> None:
    proc = _sandbox(tmp_path / "proc", started)
    keepalive = started[0]
    server, child = _spawn("sleep", "301"), _spawn("sleep", "303")
    started.extend([server, child])
    _link(proc, server)
    _link(proc, child)

    done = _stop_as_the_sandbox_runs_it(proc)

    assert _clean(done, 2), done.stdout + done.stderr
    assert _gone(server) and _gone(child), "a process a step left running outlived the stop"
    assert keepalive.poll() is None, "the sandbox's keep-alive was stopped: the sandbox would end"


def test_a_later_process_that_looks_like_the_keep_alive_is_stopped(tmp_path: Path, started: Any) -> None:
    """Only the first ``sleep infinity`` the init started keeps the sandbox alive: a later one is a step's. (Written
    into the table, it stays there after it ends, so this run of the stop does not end clean.)"""
    proc = _sandbox(tmp_path / "proc", started)
    impostor = _spawn("sleep", "302")
    started.append(impostor)
    _describe(proc, impostor.pid, 1, ["sleep", "infinity"], group=impostor.pid)
    _stop(proc)
    assert _gone(impostor), "a later look-alike of the keep-alive was kept"
    assert started[0].poll() is None, "the sandbox's keep-alive was stopped: the sandbox would end"


def test_a_group_that_keeps_coming_back_is_stopped_whole(tmp_path: Path, started: Any) -> None:
    """A process that forks a successor and exits is never the same process twice: the stop ends its whole group."""
    proc = _sandbox(tmp_path / "proc", started)
    hopper = _spawn(sys.executable, "-I", "-c", HOPPER)
    started.append(hopper)
    time.sleep(0.2)  # its successors are hopping
    _link(proc, hopper)

    done = _stop_as_the_sandbox_runs_it(proc)

    assert _clean(done, 1), done.stdout + done.stderr
    assert _gone(hopper)
    assert _group_gone(hopper.pid), "a successor of the hopping process outlived the stop"


#: A process whose first thread ends while a second keeps running: its own ``/proc`` entry then reads as ended (Z).
FIRST_THREAD_ENDS = (
    "import ctypes, threading, time\n"
    "def run():\n"
    "    while True:\n"
    "        time.sleep(0.05)\n"
    "threading.Thread(target=run).start()\n"
    "ctypes.CDLL(None).pthread_exit(None)\n"
)


def _state_and_threads(pid: int) -> tuple[str, int]:
    status = Path(f"/proc/{pid}/status").read_text()
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    return fields["State"].split()[0], int(fields["Threads"])


def test_a_process_whose_first_thread_ended_is_stopped(tmp_path: Path, started: Any) -> None:
    """Its entry reads as ended while a thread of it still runs: the stop reads its threads, and stops it."""
    proc = _sandbox(tmp_path / "proc", started)
    survivor = _spawn(sys.executable, "-I", "-c", FIRST_THREAD_ENDS)
    started.append(survivor)
    deadline = time.monotonic() + 10
    while _state_and_threads(survivor.pid) != ("Z", 2) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert _state_and_threads(survivor.pid) == ("Z", 2), "the probe's first thread did not end with its second running"
    assert survivor.poll() is None
    _link(proc, survivor)

    done = _stop_as_the_sandbox_runs_it(proc)

    assert _clean(done, 1), done.stdout + done.stderr
    assert _gone(survivor), "a process whose first thread had ended outlived the stop"


def test_an_ended_entry_whose_threads_cannot_be_read_is_not_taken_as_ended(tmp_path: Path, started: Any) -> None:
    """Not knowing that every thread of it ended is not knowing it ended: the stop does not end clean beside it."""
    proc = _sandbox(tmp_path / "proc", started)
    (proc / "4206649").mkdir()
    (proc / "4206649" / "stat").write_bytes(b"4206649 (x) Z 1 4206649 4206649 0 -1")
    (proc / "4206649" / "cmdline").write_bytes(b"")
    done = _stop(proc)
    assert done.returncode == 3 and "still running pid 4206649 (user unknown)" in done.stderr, done.stdout + done.stderr


def _ended_entry(proc: Path, pid: int, threads: int | None, thread_states: list[bytes | None] | None) -> None:
    """``pid`` written as a process whose own entry reads as ended (Z), with the kernel's count of its threads (None:
    no count to read) and the entries of its threads (None: no list of them to read; an entry None: one whose stat is
    gone by the time it is read)."""
    (proc / str(pid)).mkdir(parents=True)
    (proc / str(pid) / "stat").write_bytes(f"{pid} (x) Z 1 {pid} {pid} 0 -1".encode())
    (proc / str(pid) / "cmdline").write_bytes(b"")
    if threads is not None:
        (proc / str(pid) / "status").write_bytes(f"Name:\tx\nState:\tZ (zombie)\nThreads:\t{threads}\n".encode())
    if thread_states is None:
        return
    (proc / str(pid) / "task").mkdir()
    for n, state in enumerate(thread_states):
        tid = proc / str(pid) / "task" / str(pid + n)
        tid.mkdir()
        if state is not None:
            (tid / "stat").write_bytes(f"{pid + n} (x) ".encode() + state + b" 1 1 1 0 -1")


@pytest.mark.parametrize(
    ("threads", "thread_states", "ended"),
    [
        (1, [b"Z"], True),
        (2, [b"Z"], False),
        (1, [b"Z", None], False),
        (1, [b"Z", b"S"], False),
        (None, [b"Z"], False),
        (1, None, False),
    ],
    ids=[
        "every-thread-ended",
        "the-kernel-counts-another-thread",
        "a-thread-unread",
        "a-thread-runs",
        "no-count",
        "no-threads",
    ],
)
def test_an_ended_entry_is_taken_as_ended_only_when_every_thread_of_it_is(
    tmp_path: Path, started: Any, threads: int | None, thread_states: list[bytes | None] | None, ended: bool
) -> None:
    """The kernel's count of its threads is read first, in one read: a thread that ends between two reads of the
    thread entries cannot hide one that started in its place. A thread entry that cannot be read is taken as running."""
    proc = _sandbox(tmp_path / "proc", started)
    _ended_entry(proc, 4206650, threads, thread_states)
    done = _stop(proc)
    if ended:
        assert done.returncode == 0 and done.stdout.strip() == "0", done.stdout + done.stderr
    else:
        assert done.returncode == 3 and "pid 4206650" in done.stderr, done.stdout + done.stderr


#: A process whose first thread has ended, and each thread of which starts its successor and ends: no one thread of
#: it lives for long.
THREAD_HOPPER = (
    "import ctypes, _thread, time\n"
    "def hop():\n"
    "    _thread.start_new_thread(hop, ())\n"
    "_thread.start_new_thread(hop, ())\n"
    "time.sleep(0.05)\n"
    "ctypes.CDLL(None).pthread_exit(None)\n"
)


def test_a_process_whose_threads_keep_handing_over_is_stopped(tmp_path: Path, started: Any) -> None:
    """Each run of the stop meets a fresh one: in every run it is stopped, never reported as ended while running."""
    for trial in range(16):
        proc = _sandbox(tmp_path / f"proc{trial}", started)
        hopper = _spawn(sys.executable, "-I", "-c", THREAD_HOPPER)
        started.append(hopper)
        deadline = time.monotonic() + 10
        while _state_and_threads(hopper.pid)[0] != "Z" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert _state_and_threads(hopper.pid)[0] == "Z", "the probe's first thread did not end"
        assert hopper.poll() is None
        _link(proc, hopper)

        done = _stop_as_the_sandbox_runs_it(proc)

        assert _clean(done, 1), (trial, done.stdout, done.stderr)
        assert _gone(hopper), f"trial {trial}: the stop ended clean while the process still ran"


def test_a_leftover_in_the_keep_alives_group_ends_alone(tmp_path: Path, started: Any) -> None:
    """A leftover that joined the keep-alive's process group is stopped by itself: its group, the keep-alive's, is
    never stopped whole."""
    proc = tmp_path / "proc"
    _describe(proc, 1, 0, ["/sbin/docker-init", "--", "sleep", "infinity"])
    _quiet(proc)
    keepalive = subprocess.Popen(["sleep", "300"], process_group=0)  # a group of its own, in this session
    started.append(keepalive)
    _describe(proc, keepalive.pid, 1, ["sleep", "infinity"], group=keepalive.pid)
    joined = subprocess.Popen(["sleep", "304"], process_group=keepalive.pid)
    started.append(joined)
    _link(proc, joined)

    done = _stop_as_the_sandbox_runs_it(proc)

    assert _clean(done, 1), done.stdout + done.stderr
    assert _gone(joined)
    assert keepalive.poll() is None, "the keep-alive's group was stopped whole: the sandbox would end"


def test_a_leftover_in_the_stops_own_group_does_not_end_the_stop(tmp_path: Path, started: Any) -> None:
    """The stop never stops its own process group: it would end itself before it is done."""
    proc = _sandbox(tmp_path / "proc", started)
    leader = subprocess.Popen(["sleep", "305"], process_group=0)  # a group of its own, in this session
    started.append(leader)
    _link(proc, leader)
    done = _stop_as_the_sandbox_runs_it(proc, process_group=leader.pid)
    assert _clean(done, 1), (done.returncode, done.stdout, done.stderr)
    assert _gone(leader)


def test_a_table_that_never_comes_clean_is_no_clean_stop(tmp_path: Path, started: Any) -> None:
    """A process the stop cannot end (here: one the table names that no signal reaches) leaves it never clean: it
    says so and exits non-zero, after its passes."""
    proc = _sandbox(tmp_path / "proc", started)
    _describe(proc, 2**22 + 12345, 1, ["python3", "-c", "loop"])
    done = _stop(proc)
    assert done.returncode == 3 and not done.stdout.strip(), done.stdout + done.stderr
    assert done.stderr.strip() == f"not clean: still running pid {2**22 + 12345} (user unknown)", done.stderr


def test_a_stop_whose_passes_are_spent_ends_clean_when_nothing_is_left(tmp_path: Path, started: Any) -> None:
    """The table is read once more after the last pass: what that pass stopped is not reported as left."""
    proc = _sandbox(tmp_path / "proc", started)
    done = _stop(proc, deadline=0)
    assert done.returncode == 0 and done.stdout.strip() == "0", done.stdout + done.stderr


def test_a_stop_that_cannot_end_clean_names_what_is_left_and_whose_it_is(tmp_path: Path, started: Any) -> None:
    proc = _sandbox(tmp_path / "proc", started)
    for n in range(7):
        _describe(proc, 4206660 + n, 1, ["python3", "-c", "loop"])
    (proc / "4206660" / "status").write_bytes(b"Name:\tx\nUid:\t0\t0\t0\t0\n")
    done = _stop(proc)
    assert done.returncode == 3
    assert done.stderr.strip() == (
        "not clean: still running pid 4206660 (user root), pid 4206661 (user unknown), pid 4206662 (user unknown), "
        "pid 4206663 (user unknown), pid 4206664 (user unknown) and 2 more"
    ), done.stderr


async def test_the_sandbox_says_what_kept_the_stop_from_ending_clean(tmp_path: Path, started: Any) -> None:
    proc = _sandbox(tmp_path / "proc", started)
    _describe(proc, 4206670, 1, ["python3", "-c", "loop"])
    (proc / "4206670" / "status").write_bytes(b"Name:\tx\nUid:\t0\t0\t0\t0\n")
    with pytest.raises(LeftoversNotStopped, match=r"still running pid 4206670 \(user root\)"):
        await _manager(_LocalDocker(proc, f"stop-under-test-{uuid.uuid4().hex}")).stop_leftover_processes(
            "sandbox-job-1"
        )


def test_the_stop_does_not_stop_itself(tmp_path: Path, started: Any) -> None:
    """In the sandbox the stop's own process is in the table: it finishes, and says how many it stopped."""
    proc = _sandbox(tmp_path / "proc", started)
    itself = (
        "import os, sys\n"
        "d = os.path.join(sys.argv[1], str(os.getpid()))\n"
        "os.makedirs(d)\n"
        "open(os.path.join(d, 'stat'), 'wb').write(f'{os.getpid()} (python3) R 0 {os.getpgrp()} 0 0 -1'.encode())\n"
        "open(os.path.join(d, 'cmdline'), 'wb').write(b'python3\\0-c\\0')\n"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", itself + LEFTOVER_STOP, str(proc)], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0 and done.stdout.strip() == "0", (done.returncode, done.stdout, done.stderr)
    assert started[0].poll() is None


class _LocalDocker:
    """``docker exec`` into a sandbox whose process table is ``proc``: the command runs here, over that table, with
    a token on its command line that names this test's runs alone."""

    def __init__(self, proc: Path, token: str, *, start_after: float = 0.0) -> None:
        self.proc, self.token, self.runs, self.start_after = proc, token, 0, start_after

    def build_exec_cmd(self, container: str, cmd: list[str], **_kw: Any) -> list[str]:
        assert container == "sandbox-job-1" and cmd[1:3] == ["-I", "-c"]
        # The interpreter's start, slowed so a leftover scanning this host's large process table sees the run, as one
        # scanning a sandbox's few processes does at once.
        stop = cmd[3].replace(
            f"deadline = time.monotonic() + {PASS_DEADLINE!r}", f"deadline = time.monotonic() + {QUICK!r}"
        )
        assert stop != cmd[3]
        script = f"import time\ntime.sleep({self.start_after})\n{stop}"
        return [sys.executable, "-I", "-c", script, str(self.proc), self.token]

    async def run_cmd(self, cmd: list[str], timeout: int = 60, **_kw: Any) -> str:
        self.runs += 1
        child = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await asyncio.wait_for(child.communicate(), timeout)
        if child.returncode != 0:
            raise RuntimeError(f"exit {child.returncode}: {err.decode()[-200:]}")
        return out.decode()


def _manager(docker: Any) -> Any:
    from embry0.execution.sandbox_manager import SandboxManager

    manager = SandboxManager.__new__(SandboxManager)
    manager._docker = docker  # noqa: SLF001
    return manager


async def test_a_leftover_that_stops_the_stop_leaves_it_not_stopped(tmp_path: Path, started: Any) -> None:
    """A leftover that ends every run of the stop as it starts: each run is no clean pass, and after its runs the
    sandbox says the leftovers were not stopped (a step that may push then does not start)."""
    proc = _sandbox(tmp_path / "proc", started)
    token = f"stop-under-test-{uuid.uuid4().hex}"
    killer_source = (
        "import os, signal, sys, time\n"
        "token = sys.argv[1].encode()\n"
        "me = os.getpid()\n"
        "while True:\n"
        "    for name in os.listdir('/proc'):\n"
        "        if name.isdigit() and int(name) != me:\n"
        "            try:\n"
        "                if token in open(f'/proc/{name}/cmdline', 'rb').read():\n"
        "                    os.kill(int(name), signal.SIGKILL)\n"
        "            except OSError:\n"
        "                pass\n"
        "    time.sleep(0.0005)\n"
    )
    killer = _spawn(sys.executable, "-I", "-c", killer_source, token)
    started.append(killer)
    time.sleep(0.2)
    docker = _LocalDocker(proc, token, start_after=0.5)
    with pytest.raises(LeftoversNotStopped, match="exit -9"):
        await _manager(docker).stop_leftover_processes("sandbox-job-1")
    assert docker.runs == ATTEMPTS, "each run was stopped as it started"

    # The step that may push does not start beside it: the job fails closed.
    from embry0.workflows.issue_to_pr.step_start import starts_a_step

    ran = AsyncMock()
    config = {"configurable": {"db": None, "sandbox_manager": _manager(docker), "proxy_manager": None}}
    with pytest.raises(LeftoversNotStopped):
        await starts_a_step(ran, "developer")({"job_id": "J", "sandbox_container_id": "sandbox-job-1"}, config)
    ran.assert_not_awaited()


async def test_the_sandbox_runs_the_stop_until_one_run_ends_clean() -> None:
    from embry0.execution.agent_runner import RUNNER_INTERPRETER

    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(side_effect=lambda container, cmd, **kw: ["docker", "exec", container, *cmd])
    docker.run_cmd = AsyncMock(side_effect=[RuntimeError("exit 3: not clean: still running pid 9 (user agent)"), "2\n"])
    assert await _manager(docker).stop_leftover_processes("sandbox-job-1") == 2
    command = docker.run_cmd.await_args.args[0]
    assert command[:3] == ["docker", "exec", "sandbox-job-1"]
    assert command[3:] == leftover_stop_command() == [RUNNER_INTERPRETER, "-I", "-c", LEFTOVER_STOP]
    timeout = docker.run_cmd.await_args.kwargs["timeout"]
    assert timeout >= PASS_DEADLINE + 15, (
        "a run that uses its whole deadline must end well inside the command's timeout"
    )
    assert "embry0" not in LEFTOVER_STOP, "the stop imports nothing of embry0: a change needs no image rebuild"

    docker.run_cmd = AsyncMock(side_effect=RuntimeError("exit 137"))
    with pytest.raises(LeftoversNotStopped):
        await _manager(docker).stop_leftover_processes("sandbox-job-1")
    assert docker.run_cmd.await_count == ATTEMPTS

    docker.run_cmd = AsyncMock(return_value="")
    with pytest.raises(LeftoversNotStopped):
        await _manager(docker).stop_leftover_processes("sandbox-job-1")


def test_the_test_table_reaches_no_process_it_does_not_name(tmp_path: Path) -> None:
    """The stop reads the table it is shown: a table naming nothing signals nothing."""
    proc = tmp_path / "proc"
    _quiet(proc)
    done = _stop(proc)
    assert done.returncode == 0 and done.stdout.strip() == "0", done.stdout + done.stderr


def test_a_table_read_while_processes_are_created_is_never_clean(tmp_path: Path, started: Any) -> None:
    """A process that ends between the listing of the table and the read of its entry reads as ended, and one created
    in its place after the listing is not in the table: a read during which a process was created proves nothing.
    Here the last process id moves during every read (it is a file whose content changes as the stop reads), so no
    read is clean, though the table names nothing left to stop."""
    proc = _sandbox(tmp_path / "proc", started)
    counter = proc / "sys" / "kernel" / "ns_last_pid"
    counter.unlink()
    counter.symlink_to("/proc/self/io")  # the reading process's byte count: it grows with every entry read
    done = _stop(proc)
    assert done.returncode == 3 and not done.stdout.strip(), done.stdout + done.stderr
    assert done.stderr.strip() == "not clean: processes kept being created while the table was read", done.stderr
    assert started[0].poll() is None


def _serve(path: Path, contents: list[bytes], done: threading.Event, reads: list[int] | None = None) -> None:
    """``path``, a FIFO, gives each read its own content in turn (the last one from then on). Each read gets a fresh
    FIFO: the one being read is replaced before it is closed, so two reads never share a stream. ``reads``, when
    given, holds how many reads it served."""
    served = 0
    while not done.is_set():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:  # no reader has it open yet
            time.sleep(0.0002)
            continue
        try:
            os.write(fd, contents[min(served, len(contents) - 1)])
            fresh = path.with_name(path.name + ".fresh")
            os.mkfifo(fresh)
            os.replace(fresh, path)
        finally:
            os.close(fd)
        served += 1
        if reads is not None:
            reads[:] = [served]


def test_each_process_is_stopped_with_the_group_the_read_that_found_it_gave(tmp_path: Path, started: Any) -> None:
    """A process can end between two reads of the table, and its entry then names no group: the group the stop ends
    with it is the one the read that found it gave. Here the entry's first two reads (the stop's first look, then its
    first pass) name the group of a real process this test started; every later read names a group that does not
    exist."""
    proc = _sandbox(tmp_path / "proc", started)
    member = subprocess.Popen(["sleep", "306"], process_group=0)  # a group of its own, in this session
    started.append(member)
    entry = proc / "4206700"
    entry.mkdir()
    (entry / "cmdline").write_bytes(b"python3\0-c\0loop\0")
    os.mkfifo(entry / "stat")
    found = f"4206700 (x) S 1 {member.pid} {member.pid} 0 -1".encode()
    done = threading.Event()
    feeder = threading.Thread(
        target=_serve, args=(entry / "stat", [found, found, b"4206700 (x) S 1 4206790 4206790 0 -1"], done)
    )
    feeder.start()
    try:
        _stop(proc)
    finally:
        done.set()
        feeder.join(10)
    assert not feeder.is_alive()
    assert _gone(member), "the group the first pass found was not stopped"


def _slow_to_end(proc: Path, started: Any, reads_running: int) -> tuple[Path, list[bytes]]:
    """A real process, ended but not yet reaped (so a signal still reaches it), in the table as an entry that reads as
    running for its first ``reads_running`` reads (the stop's first look, then one per pass), then as ended. The
    entry, and what its reads give, in turn."""
    slow = _spawn("sleep", "307")
    started.append(slow)
    slow.send_signal(9)
    deadline = time.monotonic() + 10
    while _state_and_threads(slow.pid)[0] != "Z" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert _state_and_threads(slow.pid)[0] == "Z"
    entry = proc / str(slow.pid)
    (entry / "task" / str(slow.pid)).mkdir(parents=True)
    (entry / "task" / str(slow.pid) / "stat").write_bytes(f"{slow.pid} (sleep) Z 1 0 0 0 -1".encode())
    (entry / "status").write_bytes(b"Name:\tsleep\nState:\tZ (zombie)\nThreads:\t1\n")
    (entry / "cmdline").write_bytes(b"sleep\x00307\x00")
    os.mkfifo(entry / "stat")
    running = f"{slow.pid} (sleep) S 1 0 0 0 -1".encode()
    ended = f"{slow.pid} (sleep) Z 1 0 0 0 -1".encode()
    return entry, [running] * reads_running + [ended]


def _stop_beside(entry: Path, contents: list[bytes], proc: Path, deadline: float) -> tuple[Any, int]:
    """One run of the stop while ``entry``'s stat gives ``contents``; the run, and how many reads the entry served."""
    done_feeding, reads = threading.Event(), [0]
    feeder = threading.Thread(target=_serve, args=(entry / "stat", contents, done_feeding, reads))
    feeder.start()
    try:
        done = _stop(proc, deadline=deadline)
    finally:
        done_feeding.set()
        feeder.join(10)
    assert not feeder.is_alive()
    return done, reads[0]


def test_a_process_signalled_on_more_than_one_pass_is_counted_once(tmp_path: Path, started: Any) -> None:
    """A process slow to end after it is signalled is still in the table at the next pass, and is signalled again: it
    is one process stopped."""
    proc = _sandbox(tmp_path / "proc", started)
    entry, contents = _slow_to_end(proc, started, reads_running=3)
    done, _ = _stop_beside(entry, contents, proc, QUICK)
    assert done.returncode == 0 and done.stdout.strip() == "1", done.stdout + done.stderr


def test_a_process_slow_to_end_is_waited_for_until_the_deadline(tmp_path: Path, started: Any) -> None:
    """The passes go on until the deadline, not for a set number: a process that reads as running for 60 passes, as
    one freeing gigabytes on a busy host does, still ends in a clean run."""
    proc = _sandbox(tmp_path / "proc", started)
    entry, contents = _slow_to_end(proc, started, reads_running=61)
    done, reads = _stop_beside(entry, contents, proc, PASS_DEADLINE)
    assert done.returncode == 0 and done.stdout.strip() == "1", done.stdout + done.stderr
    assert reads >= 62


def test_the_passes_pause_so_a_process_gets_a_cpu_to_end_on(tmp_path: Path, started: Any) -> None:
    """Between two passes the stop sleeps: over a run that never comes clean it makes about a pass per pause, not as
    many as it can."""
    proc = _sandbox(tmp_path / "proc", started)
    entry, contents = _slow_to_end(proc, started, reads_running=10**6)
    began = time.monotonic()
    done, reads = _stop_beside(entry, contents, proc, QUICK)
    took = time.monotonic() - began
    assert done.returncode == 3, done.stdout + done.stderr
    assert took >= QUICK
    assert 3 <= reads <= QUICK / PASS_PAUSE + 5, reads


def test_a_table_whose_last_process_id_cannot_be_read_is_never_clean(tmp_path: Path, started: Any) -> None:
    proc = _sandbox(tmp_path / "proc", started)
    (proc / "sys" / "kernel" / "ns_last_pid").unlink()
    done = _stop(proc)
    assert done.returncode == 3 and "kept being created" in done.stderr, done.stdout + done.stderr
