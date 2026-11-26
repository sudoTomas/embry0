"""What one step of a job leaves running in its sandbox, stopped before the next step starts (#395).

Every step of an issue-to-pr job runs in the job's one sandbox. A process a step starts in the background outlives
the step: its runner's ``docker exec`` ends, the process is reparented to the sandbox's init, and it keeps running
into the next step, the one that may push the job's branch included. Before each step starts, every process in the
sandbox is stopped but its init (PID 1, ``docker run --init``), the process that keeps the sandbox alive (the first
``sleep infinity`` the init started, ``DockerClient.build_run_cmd``) and the stopping process itself.

Each process found is stopped with its whole process group, so one that forks a successor and exits is stopped with
every successor its group holds. A process every thread of which has ended, waiting only for its parent to read its
status, is left out: it runs nothing. One whose first thread has ended while another still runs reads as ended in
its own entry, so its threads are read too: it is stopped.

The stop goes over the process table again until a pass finds nothing left to stop (at most :data:`PASSES` passes).
A pass finds nothing only when no process was created while it read the table: the sandbox's last process id
(``sys/kernel/ns_last_pid``) is the same before and after it, so a process created after the table was listed, in
place of one that ended before it was read, is not missed. A stop that never gets a pass like that exits non-zero,
naming the processes still running and their users; so does a stop that is itself stopped before it ends. The
sandbox manager runs it again a few times (:data:`ATTEMPTS`); when no run ends clean it raises
:class:`LeftoversNotStopped`, and a step that may push does not start (``embry0.workflows.issue_to_pr.step_start``).

The stop runs inside the sandbox as the sandbox's user, with its own interpreter in isolated mode, and imports
nothing of embry0: a change here needs no sandbox image rebuild. It reads the process table from ``/proc``; a test
points it at another directory (its first argument), so it never reads, or signals, a process it was not shown.
"""

from __future__ import annotations

from typing import Final

#: How many times one run of the stop goes over the process table for a pass that finds nothing left to stop.
PASSES: Final = 20
#: How many runs of the stop the sandbox manager makes before it gives up (``SandboxManager.stop_leftover_processes``).
ATTEMPTS: Final = 3

#: The stop, as the sandbox's interpreter runs it (``-I -c``). It prints how many processes it stopped and exits 0
#: once a pass finds nothing left to stop; it exits 3 when no pass of its :data:`PASSES` did, naming on stderr the
#: processes still running (at most five) and their users.
LEFTOVER_STOP: Final = r"""
import os, signal, sys, time
root = sys.argv[1] if len(sys.argv) > 1 else "/proc"
me = os.getpid()


def running(entry):
    # Any thread of it may still run (its first may have ended, which makes the process itself read as ended). The
    # kernel's count of its threads comes first, in one read: more than the one that ended is a thread still running,
    # however briefly each lives. Anything that cannot be read is taken as running.
    try:
        with open(os.path.join(entry, "status"), "rb") as f:
            for line in f:
                if line.startswith(b"Threads:") and int(line.split()[1]) > 1:
                    return True
    except (OSError, ValueError, IndexError):
        return True
    try:
        threads = os.listdir(os.path.join(entry, "task"))
    except OSError:
        return True
    for tid in threads:
        try:
            with open(os.path.join(entry, "task", tid, "stat"), "rb") as f:
                stat = f.read()
            if stat[stat.rindex(b")") + 2:].split()[0] not in (b"Z", b"X"):
                return True
        except (OSError, ValueError, IndexError):
            return True
    return False


def owner(pid):
    try:
        with open(os.path.join(root, str(pid), "status"), "rb") as f:
            for line in f:
                if line.startswith(b"Uid:"):
                    uid = int(line.split()[1])
                    try:
                        import pwd

                        return pwd.getpwuid(uid).pw_name
                    except (ImportError, KeyError):
                        return "uid " + str(uid)
    except (OSError, ValueError, IndexError):
        pass
    return "unknown"


def table():
    found = {}
    for name in os.listdir(root):
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(root, name, "stat"), "rb") as f:
                stat = f.read()
            with open(os.path.join(root, name, "cmdline"), "rb") as f:
                cmdline = f.read()
            fields = stat[stat.rindex(b")") + 2:].split()
            if fields[0] in (b"Z", b"X") and not running(os.path.join(root, name)):
                continue  # every thread ended, waiting for its parent to read its status: nothing left to stop
            found[int(name)] = (int(fields[1]), int(fields[2]), cmdline)
        except (OSError, ValueError, IndexError):
            continue
    return found


def last_pid():
    try:
        with open(os.path.join(root, "sys", "kernel", "ns_last_pid"), "rb") as f:
            return f.read().strip()
    except OSError:
        return None


def quiet_table():
    # The table, and whether no process was created while it was read (unknown counts as created).
    before = last_pid()
    found = table()
    after = last_pid()
    return found, before is not None and before == after


first = table()
keepalive = min((p for p, (ppid, _g, c) in first.items() if ppid == 1 and c == b"sleep\0infinity\0"), default=None)
keep = {1, me} | ({keepalive} if keepalive is not None else set())
spared_groups = {0, 1, os.getpgrp()} | {first[p][1] for p in keep if p in first}
stopped = 0
for _ in range(PASSES):
    found, quiet = quiet_table()
    targets = [p for p in sorted(found) if p not in keep]
    if not targets and quiet:
        print(stopped)
        sys.exit(0)
    for pid in targets:
        group = found[pid][1]
        if group not in spared_groups:
            try:
                os.killpg(group, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            os.kill(pid, signal.SIGKILL)
            stopped += 1
        except (ProcessLookupError, PermissionError):
            pass
    time.sleep(0.005)
found, quiet = quiet_table()
left = [p for p in sorted(found) if p not in keep]
if not left and quiet:
    print(stopped)
    sys.exit(0)
if not left:
    sys.stderr.write("not clean: processes kept being created while the table was read\n")
    sys.exit(3)
named = ", ".join("pid %d (user %s)" % (p, owner(p)) for p in left[:5])
more = " and %d more" % (len(left) - 5) if len(left) > 5 else ""
sys.stderr.write("not clean: still running " + named + more + "\n")
sys.exit(3)
""".replace("PASSES", str(PASSES))


class LeftoversNotStopped(RuntimeError):
    """No run of the stop ended with a pass that found nothing left to stop in the sandbox (#395)."""

    def __init__(self, container: str, why: str) -> None:
        super().__init__(
            f"processes an earlier step left running in the sandbox could not all be stopped ({why}), so a step "
            "that may push the job's branch does not start beside them"
        )
        self.container = container


def leftover_stop_command() -> list[str]:
    """The command that stops what earlier steps left running, for ``docker exec`` into the sandbox."""
    from embry0.execution.agent_runner import RUNNER_INTERPRETER

    return [RUNNER_INTERPRETER, "-I", "-c", LEFTOVER_STOP]
