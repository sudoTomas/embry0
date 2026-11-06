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

It repeats kill passes until a pass finds nothing to stop, at most
:data:`MAX_PASSES` times, ignores zombies (they have already exited) and
reports what is still running after the last pass: a survivor means the
sandbox could not be cleared, and the caller does not trust what it reads
next.

:func:`select_to_stop` is the selection as a function of a process table, and
:func:`render_script` writes the same rules as the in-sandbox script;
``tests/unit/workflows/verification/test_sweep.py`` runs that script against a
process table of its own and checks the two agree.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

import structlog

from embry0.safety.redaction import known_secret_values
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

_REPORT_RE = re.compile(
    rf"{REPORT_WORD} stopped=([0-9]+) passes=([0-9]+) survivors=([0-9]+) pids=((?:[0-9]+(?:,[0-9]+)*)?)"
)
_PROC_ROOT_RE = re.compile(r"/[A-Za-z0-9._/-]*")


@dataclass(frozen=True)
class Process:
    """One row of a process table, as the sweep reads it from ``/proc``.

    ``start`` is the start time in clock ticks since boot (field 22 of
    ``/proc/<pid>/stat``), None when it could not be read; ``uid`` the real
    uid and ``state`` the one-letter state of ``/proc/<pid>/status``.
    """

    pid: int
    uid: int
    state: str
    start: int | None


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
    return sorted(p.pid for p in rows if p.pid not in keep and p.uid == uid and p.state not in ZOMBIE_STATES)


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
        "me=$$ uid=$EUID passes=0 survivors=0 pids=\n"
        "declare -A hit=()\n"
        "sel=()\n"
        "embry0_sweep_scan() {\n"
        "  sel=()\n"
        "  local d p k v x line rest start st u i keeper= oldest=\n"
        "  local -a f dirs all=() uids=() states=()\n"
        # Globbing is off for the field split below; on only to list the table.
        "  set +f\n"
        f"  dirs=({shlex.quote(proc_root)}/[0-9]*)\n"
        "  set -f\n"
        '  for d in "${dirs[@]}"; do\n'
        "    p=${d##*/} u= st= line=\n"
        "    while read -r k v x; do\n"
        "      case $k in\n"
        "        State:) st=$v ;;\n"
        "        Uid:) u=$v; break ;;\n"
        "      esac\n"
        '    done < "$d/status"\n'
        '    [ -n "$u" ] || continue\n'
        # The whole file, not one line: a command name can hold a newline.
        "    read -r -d '' line < \"$d/stat\"\n"
        # Fields after the command name, which ends at the LAST ") ".
        "    rest=${line##*) }\n"
        "    f=($rest)\n"
        "    start=${f[19]}\n"
        '    all+=("$p") uids+=("$u") states+=("$st")\n'
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
        f"    case ${{states[i]}} in {zombies}) continue ;; esac\n"
        '    sel+=("$p")\n'
        "  done\n"
        "}\n"
        "while :; do\n"
        "  embry0_sweep_scan\n"
        '  [ "${#sel[@]}" -eq 0 ] && break\n'
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
        f"printf '{REPORT_WORD} stopped=%d passes=%d survivors=%d pids=%s\\n' "
        '"${#hit[@]}" "$passes" "$survivors" "$pids"\n'
    )


#: The script every sweep runs.
SCRIPT: Final = render_script()


@dataclass(frozen=True)
class SweepResult:
    """What one sweep found. ``clean`` only when it ran and nothing it should stop is left."""

    clean: bool
    stopped: int = 0
    passes: int = 0
    survivors: int = 0
    pids: tuple[int, ...] = ()

    def describe(self) -> str:
        """Why the sweep is not clean, as a clause for a note."""
        if self.clean:
            return "the sandbox was cleared"
        if not self.survivors:
            return "the sandbox's processes could not be checked"
        named = ", ".join(str(p) for p in self.pids)
        more = " and more" if self.survivors > len(self.pids) else ""
        noun = "process" if self.survivors == 1 else "processes"
        pid_word = "pid" if len(self.pids) == 1 else "pids"
        return f"{self.survivors} {noun} could not be stopped ({pid_word} {named}{more})"


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
    stopped, passes, survivors = (int(found.group(n)) for n in (1, 2, 3))
    pids = tuple(int(p) for p in found.group(4).split(",")) if found.group(4) else ()
    if passes > MAX_PASSES or len(pids) > REPORT_PIDS or len(pids) != min(survivors, REPORT_PIDS):
        return _UNCHECKED
    return SweepResult(clean=survivors == 0, stopped=stopped, passes=passes, survivors=survivors, pids=pids)


def _excerpt(text: str) -> str:
    """Up to 300 characters of sandbox output for a log line: scrubbed first, so a cut leaves no fragment."""
    return scrub_output(text, known_values=known_secret_values())[:300]


async def sweep_sandbox(docker: Any, container_id: str) -> SweepResult:
    """Run the sweep in ``container_id`` as its default user; never raises."""
    try:
        cmd = docker.build_exec_cmd(container_id, ["bash", "-c", SCRIPT])
        returncode, stdout, stderr = await docker.run_cmd_status(cmd, timeout=SWEEP_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - a sweep that cannot run is an unclean sweep, not a crash
        logger.warning("sandbox_sweep_failed", container_id=container_id, error=str(exc)[:300])
        return _UNCHECKED
    result = read_report(returncode, stdout, stderr)
    if result.survivors:
        logger.warning(
            "sandbox_sweep_survivors", container_id=container_id, survivors=result.survivors, pids=list(result.pids)
        )
    elif not result.clean:
        logger.warning(
            "sandbox_sweep_unreadable",
            container_id=container_id,
            returncode=returncode,
            stdout=_excerpt(stdout),
            stderr=_excerpt(stderr),
        )
    elif result.stopped:
        logger.info("sandbox_sweep_stopped", container_id=container_id, stopped=result.stopped, passes=result.passes)
    return result
