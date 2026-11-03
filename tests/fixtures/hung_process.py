"""A command that hangs with a grandchild holding its stdout, and a run of it that reports what it leaves behind.

Killing only the command a coroutine started is not enough when that command
has children: the compose plugin `docker compose` runs keeps the stdout pipe
open, `Process.wait()` waits for the pipes to close as well as for the process,
and the process is left to the garbage collector. Collected after its event
loop closed, its transport raises from ``__del__`` (pytest's
``PytestUnraisableExceptionWarning``). The script here starts a grandchild that
outlives any wait window a caller might allow (#336).
"""

from __future__ import annotations

import asyncio
import gc
import os
import signal
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

#: How long the grandchild sleeps: longer than any wait for a killed command to be reaped.
GRANDCHILD_SECONDS = 60


def hung_script(pidfile: Path) -> str:
    """A shell script that backgrounds a long sleep holding stdout, records its pid, then waits for it."""
    return f"sleep {GRANDCHILD_SECONDS} & echo $! > {pidfile}; wait"


@dataclass(frozen=True)
class Leftovers:
    #: How long the call took to raise.
    seconds: float
    #: What raised from ``__del__`` once the event loop had closed.
    unraisable: list[str]
    #: Whether the grandchild was still running after the call raised.
    grandchild_alive: bool


def _running(pid: int) -> bool:
    """Whether ``pid`` runs; a zombie runs nothing."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def run_until_it_raises(call: Callable[[], Awaitable[Any]], expected: type[BaseException], pidfile: Path) -> Leftovers:
    """Run ``call`` in an event loop of its own until it raises ``expected``, close the loop, collect garbage, and
    report what is left. A grandchild still running is killed here: the test started it."""
    unraisable: list[str] = []
    previous = sys.unraisablehook
    sys.unraisablehook = lambda u: unraisable.append(f"{u.exc_type.__name__}: {u.exc_value} ({u.object!r})")
    try:
        started = time.monotonic()
        with pytest.raises(expected):
            asyncio.run(call())  # type: ignore[arg-type]
        seconds = time.monotonic() - started
        gc.collect()
    finally:
        sys.unraisablehook = previous
    pid = int(pidfile.read_text()) if pidfile.exists() and pidfile.read_text().strip() else None
    deadline = time.monotonic() + 2
    while pid is not None and _running(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    alive = pid is not None and _running(pid)
    if alive and pid is not None:
        os.kill(pid, signal.SIGKILL)
    return Leftovers(seconds=seconds, unraisable=unraisable, grandchild_alive=alive)
