"""Stop the processes a sandbox has left running, before and after each test run.

The dev sandbox outlives the steps of a job: an agent can leave a server, a
watcher or a loop running in it, and a test run can leave processes of its
own. Any of them could touch the files or the output a later step reads. The
gate therefore sweeps the sandbox before and after every run, and the verify
node once before it computes the change's file list.

A sweep is one ``docker exec`` as the container's default user, a bash script
that uses bash builtins only, so it starts no process of its own. It stops
every process that user owns except three:

- PID 1, the container's init (sandboxes start ``docker run -d --init``);
- the container's long-running command, ``sleep infinity``: the oldest
  process other than PID 1, by start time (the lower PID on a tie). The
  container stops when it ends;
- the sweep's own process.

It repeats kill passes until a pass finds nothing to stop and could read
every process it listed, at most :data:`MAX_PASSES` times. It ignores
zombies, which have already exited; a process whose main thread exited
while others still run also reads as state ``Z``, so a zombie is one with a
single thread. It reports what is still running, and how many processes it
could not read, after the last pass: either means the sandbox could not be
cleared, and the caller does not trust what it reads next.

The exec runs with ``LC_ALL=C`` (:data:`EXEC_ENV`): the sweep runs no
project code, and bash prints a setlocale warning on stderr when it starts
under a locale the image lacks, which would make every report unreadable.

:func:`select_to_stop` is the selection as a function of a process table, and
:func:`render_script` writes the same rules as the in-sandbox script;
``tests/unit/workflows/verification/test_sweep.py`` runs that script against a
process table of its own and checks the two agree.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Final

import structlog

from embry0.safety.redaction import secret_env_values
from embry0.workflows.verification.scrub import scrub_output

logger = structlog.get_logger(__name__)

#: Kill passes before the sweep gives up and reports what is left.
MAX_PASSES: Final = 20
#: The pause after a pass that stopped something, so a killed process has
#: exited before the next pass reads the table; at most about 5 s in all.
PASS_WAIT_SECONDS: Final = 0.25
#: Survivors named in the report; the count covers the rest.
REPORT_PIDS: Final = 20
#: The sweep's own exec: the passes, their pauses and reading the table.
SWEEP_TIMEOUT_SECONDS: Final = 60
#: Process states that have already exited: a zombie waits only to be reaped.
ZOMBIE_STATES: Final = frozenset({"Z", "X", "x"})
#: The first word of the line the script prints, its only output.
REPORT_WORD: Final = "EMBRY0_SWEEP"
#: The sweep exec's environment: a locale every image has, whatever the sandbox's own says.
EXEC_ENV: Final = {"LC_ALL": "C"}

_REPORT_RE = re.compile(
    rf"{REPORT_WORD} stopped=([0-9]+) passes=([0-9]+) survivors=([0-9]+) unread=([0-9]+)"
    r" pids=((?:[0-9]+(?:,[0-9]+)*)?)"
)
_PROC_ROOT_RE = re.compile(r"/[A-Za-z0-9._/-]*")


@dataclass(frozen=True)
class Process:
    """One row of a process table, as the sweep reads it from ``/proc``.

    ``start`` is the start time in clock ticks since boot (field 22 of
    ``/proc/<pid>/stat``), None when it could not be read; ``uid`` the real
    uid, ``state`` the one-letter state and ``threads`` the thread count of
    ``/proc/<pid>/status`` (None when it could not be read).
    """

    pid: int
    uid: int
    state: str
    start: int | None
    threads: int | None = 1


def is_zombie(process: Process) -> bool:
    """Exited, waiting only to be reaped. State ``Z`` with live threads is a process whose main thread exited."""
    return process.state in ZOMBIE_STATES and process.threads == 1


def long_running_command(table: Iterable[Process]) -> int | None:
    """The container's own command: the oldest process other than PID 1, the lower PID on a tie."""
    candidates = [p for p in table if p.pid != 1 and p.start is not None]
    if not candidates:
        return None
    return min(candidates, key=lambda p: (p.start, p.pid)).pid


def select_to_stop(table: Iterable[Process], *, uid: int, self_pid: int) -> list[int]:
    """The PIDs one pass stops: every live process of ``uid`` but PID 1, the container's command and the sweep."""
    rows = list(table)
    keep = {1, self_pid}
    command = long_running_command(rows)
    if command is not None:
        keep.add(command)
    return sorted(p.pid for p in rows if p.pid not in keep and p.uid == uid and not is_zombie(p))


def render_script(
    *,
    proc_root: str = "/proc",
    signal: str = "kill -KILL",
    max_passes: int = MAX_PASSES,
    pass_wait: float = PASS_WAIT_SECONDS,
) -> str:
    """The sweep as a bash script of builtins only, for ``bash -c``.

    Production runs :data:`SCRIPT`, the defaults. The parameters exist so a
    test can run the same script on a process table it wrote (``proc_root``)
    with a ``signal`` command that only records what it was asked to stop.
    """
    if not _PROC_ROOT_RE.fullmatch(proc_root):
        raise ValueError(f"proc_root must be an absolute path of plain characters, not {proc_root!r}")
    if isinstance(max_passes, bool) or not isinstance(max_passes, int) or max_passes < 1:
        raise ValueError(f"max_passes must be a positive integer, not {max_passes!r}")
    if pass_wait < 0:
        raise ValueError(f"pass_wait must not be negative, not {pass_wait!r}")
    zombies = "|".join(sorted(ZOMBIE_STATES))
    # A pty master nothing writes to: `read -t` on it waits without starting a
    # process (there is no sleep builtin). Without one, the passes run back to back.
    wait_open = "exec 9<>/dev/ptmx\n" if pass_wait > 0 else ""
    wait = f"  read -r -t {pass_wait:g} _ <&9\n" if pass_wait > 0 else ""
    return (
        "set -f\n"
        "exec 2>/dev/null\n"
        f"{wait_open}"
        "me=$$ uid=$EUID passes=0 survivors=0 unread=0 pids=\n"
        "declare -A hit=()\n"
        "sel=()\n"
        "embry0_sweep_scan() {\n"
        "  sel=() unread=0\n"
        "  local d p k v x line rest start st u th i keeper= oldest=\n"
        "  local -a f dirs all=() uids=() states=() threads=()\n"
        # Globbing is off for the field split below; on only to list the table.
        "  set +f\n"
        f"  dirs=({shlex.quote(proc_root)}/[0-9]*)\n"
        "  set -f\n"
        '  for d in "${dirs[@]}"; do\n'
        "    p=${d##*/} u= st= th= line=\n"
        # Threads: comes after Uid:, so the loop reads on to it.
        "    while read -r k v x; do\n"
        "      case $k in\n"
        "        State:) st=$v ;;\n"
        "        Uid:) u=$v ;;\n"
        "        Threads:) th=$v; break ;;\n"
        "      esac\n"
        '    done < "$d/status"\n'
        # A process whose PID could not be read keeps the scan open: it may
        # have exited, or be one this pass cannot see yet.
        '    [ -n "$u" ] || { unread=$((unread + 1)); continue; }\n'
        # The whole file, not one line: a command name can hold a newline.
        "    read -r -d '' line < \"$d/stat\"\n"
        # Fields after the command name, which ends at the LAST ") ".
        "    rest=${line##*) }\n"
        "    f=($rest)\n"
        "    start=${f[19]}\n"
        '    all+=("$p") uids+=("$u") states+=("$st") threads+=("$th")\n'
        '    [ "$p" = 1 ] && continue\n'
        "    case $start in ''|*[!0-9]*) continue ;; esac\n"
        '    if [ -z "$keeper" ] || [ "$start" -lt "$oldest" ] || '
        '{ [ "$start" -eq "$oldest" ] && [ "$p" -lt "$keeper" ]; }; then\n'
        "      keeper=$p oldest=$start\n"
        "    fi\n"
        "  done\n"
        '  for i in "${!all[@]}"; do\n'
        "    p=${all[i]}\n"
        '    case $p in 1|"$me"|"$keeper") continue ;; esac\n'
        '    [ "${uids[i]}" = "$uid" ] || continue\n'
        # A zombie has exited; one with live threads has not.
        f'    case ${{states[i]}} in {zombies}) [ "${{threads[i]}}" = 1 ] && continue ;; esac\n'
        '    sel+=("$p")\n'
        "  done\n"
        "}\n"
        "while :; do\n"
        "  embry0_sweep_scan\n"
        '  [ "${#sel[@]}" -eq 0 ] && [ "$unread" -eq 0 ] && break\n'
        f'  if [ "$passes" -ge {max_passes} ]; then\n'
        "    survivors=${#sel[@]}\n"
        f'    for p in "${{sel[@]:0:{REPORT_PIDS}}}"; do pids+=${{pids:+,}}$p; done\n'
        "    break\n"
        "  fi\n"
        "  passes=$((passes + 1))\n"
        '  for p in "${sel[@]}"; do\n'
        f'    {signal} "$p"\n'
        "    hit[$p]=1\n"
        "  done\n"
        f"{wait}"
        "done\n"
        f"printf '{REPORT_WORD} stopped=%d passes=%d survivors=%d unread=%d pids=%s\\n' "
        '"${#hit[@]}" "$passes" "$survivors" "$unread" "$pids"\n'
    )


#: The script every sweep runs.
SCRIPT: Final = render_script()


@dataclass(frozen=True)
class SweepResult:
    """What one sweep found. ``clean`` only when it ran, nothing it should stop is left and it read every process."""

    clean: bool
    stopped: int = 0
    passes: int = 0
    survivors: int = 0
    pids: tuple[int, ...] = ()
    unread: int = 0

    def describe(self) -> str:
        """Why the sweep is not clean, as a clause for a note."""
        if self.clean:
            return "the sandbox was cleared"
        if not self.survivors and not self.unread:
            return "the sandbox's processes could not be checked"
        parts = []
        if self.survivors:
            named = ", ".join(str(p) for p in self.pids)
            more = " and more" if self.survivors > len(self.pids) else ""
            pid_word = "pid" if len(self.pids) == 1 else "pids"
            parts.append(f"{_count(self.survivors)} could not be stopped ({pid_word} {named}{more})")
        if self.unread:
            parts.append(f"{_count(self.unread)} could not be read")
        return ", and ".join(parts)


def _count(n: int) -> str:
    return f"{n} process" if n == 1 else f"{n} processes"


_UNCHECKED = SweepResult(clean=False)


def read_report(returncode: int, stdout: str, stderr: str) -> SweepResult:
    """The sweep's result from its exec: exit 0, nothing on stderr and exactly one well-formed line.

    Anything else, an extra line among them, is a sweep that cannot be read:
    a process it was about to stop could have written into its output.
    """
    if returncode != 0 or stderr or not stdout.endswith("\n"):
        return _UNCHECKED
    found = _REPORT_RE.fullmatch(stdout[:-1])
    if found is None:
        return _UNCHECKED
    stopped, passes, survivors, unread = (int(found.group(n)) for n in (1, 2, 3, 4))
    pids = tuple(int(p) for p in found.group(5).split(",")) if found.group(5) else ()
    if passes > MAX_PASSES or len(pids) > REPORT_PIDS or len(pids) != min(survivors, REPORT_PIDS):
        return _UNCHECKED
    return SweepResult(
        clean=survivors == 0 and unread == 0,
        stopped=stopped,
        passes=passes,
        survivors=survivors,
        pids=pids,
        unread=unread,
    )


def _excerpt(text: str, known: Sequence[str]) -> str:
    """Up to 300 characters of sandbox output for a log line: scrubbed first, so a cut leaves no fragment."""
    return scrub_output(text, known_values=known)[:300]


async def sweep_sandbox(docker: Any, container_id: str, *, scrub_values: Sequence[str] | None = None) -> SweepResult:
    """Run the sweep in ``container_id`` as its default user; never raises.

    ``scrub_values`` are the secrets the excerpts of an unreadable report are scrubbed with by value: the job's
    own, as ``run_gate`` is given them. Without them, the registry's and this process's (``secret_env_values``).
    """
    try:
        cmd = docker.build_exec_cmd(container_id, ["bash", "-c", SCRIPT], env=dict(EXEC_ENV))
        returncode, stdout, stderr = await docker.run_cmd_status(cmd, timeout=SWEEP_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - a sweep that cannot run is an unclean sweep, not a crash
        logger.warning("sandbox_sweep_failed", container_id=container_id, error=str(exc)[:300])
        return _UNCHECKED
    result = read_report(returncode, stdout, stderr)
    if result.survivors or result.unread:
        logger.warning(
            "sandbox_sweep_survivors",
            container_id=container_id,
            survivors=result.survivors,
            pids=list(result.pids),
            unread=result.unread,
        )
    elif not result.clean:
        known = list(scrub_values) if scrub_values is not None else secret_env_values()
        logger.warning(
            "sandbox_sweep_unreadable",
            container_id=container_id,
            returncode=returncode,
            stdout=_excerpt(stdout, known),
            stderr=_excerpt(stderr, known),
        )
    elif result.stopped:
        logger.info("sandbox_sweep_stopped", container_id=container_id, stopped=result.stopped, passes=result.passes)
    return result
