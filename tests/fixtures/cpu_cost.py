"""What a matcher's work costs, measured so the host's speed and load divide out.

A test that bounds a matcher's time with the wall clock (``time.perf_counter() - started < 0.5``) times the
host, not the matcher: on a loaded host the thread waits for a CPU, and a collection of the whole process's
heap that the work's allocations start lands in its time. Neither is the matcher's work, and a bound of a few
tenths of a second fails at load 50 (#558, and the glob test's 1.13 s at main's ``751bc2a0``).

What such a test is there to catch is a matcher that backtracks: one whose work grows with the text far faster
than a pass over it. So it measures the work on this thread's CPU clock (``time.thread_time``: what the thread
ran, not what it waited for), the garbage collector held, and divides it by a pass of Python's ``re`` over the
same characters (:data:`REFERENCE`, replacing each), measured on the same thread just before: a linear pass in
C, whose cost moves with the host's speed and load as the work's does. A bounded matcher measures a small multiple of that
pass; a backtracking one measures thousands of times it and more on the texts built to make it backtrack.

Each test file keeps its own limit, measured: the most its matchers cost, and the least an unbounded matcher
does on a text of the same shape, with the limit between them and a test showing the check fails on the
unbounded one.
"""

from __future__ import annotations

import gc
import math
import re
import time
from collections.abc import Callable, Iterable
from typing import Final

#: A linear pass in C: every character replaced, whatever it is, so its cost depends on the text's length only.
#: What a matcher's work is measured against.
REFERENCE: Final = re.compile(r"(?s).")
#: How long the reference must run for its clock to be read well, at the least: a pass over a short text is repeated
#: until it runs this long, or :data:`_TICKS` of the clock's resolution if those are longer.
_REFERENCE_AT_LEAST: Final = 2e-4
#: How many of the clock's ticks the reference must run for, so a clock read in coarse steps is read within about a
#: tenth of it.
_TICKS: Final = 10
#: The most repetitions of the reference pass, however short its text.
_MOST_REPETITIONS: Final = 100_000


class ClockTooCoarse(AssertionError):
    """The thread's CPU clock read no time for work it ran: it moves in steps longer than the work, so a measure of
    the work would be zero, and a bound on it would pass measuring nothing."""


def _now() -> float:
    return time.thread_time()


def resolution() -> float:
    """The step in which :func:`_now`'s clock moves, as the platform says it."""
    return time.get_clock_info("thread_time").resolution


def cpu_seconds(work: Callable[[], object], *, times: int = 1) -> float:
    """This thread's CPU time for ``work()`` run ``times`` times, the garbage collector held."""
    collecting = gc.isenabled()
    gc.disable()
    try:
        started = _now()
        for _ in range(times):
            work()
        return _now() - started
    finally:
        if collecting:
            gc.enable()


def _read(seconds: float, what: str) -> float:
    """``seconds`` as a reading of ``what``, refusing a zero, which no work costs: the clock moved in steps longer
    than the work (:class:`ClockTooCoarse`)."""
    if seconds <= 0:
        raise ClockTooCoarse(
            f"{what} read 0 s on time.thread_time, whose resolution is {resolution():g} s: the clock is too coarse to "
            "measure it, and a bound on it would pass measuring nothing"
        )
    return seconds


def least_cpu_seconds(prepare: Callable[[], Callable[[], object]], *, runs: int = 3) -> float:
    """The least, over ``runs`` runs, of the CPU time of one call of the work ``prepare()`` returns.

    ``prepare`` runs first each time, untimed: it builds what the work needs fresh (a compiled pattern a cache
    would otherwise hold warm from the run before, with what it remembered of the text), so each run times the
    work as its first call does it. A run the clock reads as no time is refused (:class:`ClockTooCoarse`).
    """
    best = math.inf
    for _ in range(runs):
        work = prepare()
        best = min(best, _read(cpu_seconds(work), "the work"))
    return best


def reference_seconds(texts: Iterable[str], *, runs: int = 3) -> float:
    """The least, over ``runs`` runs, of the CPU time of one pass of :data:`REFERENCE` over every text, each
    replacing every character. A short text's pass is repeated until it runs long enough to read."""
    chunks = list(texts)

    def one_pass() -> None:
        for chunk in chunks:
            REFERENCE.sub("<m>", chunk)

    # Doubled until the clock reads it running long enough: an estimate from one short pass would be the clock's
    # step, or zero, on a clock that moves in coarse steps.
    at_least = max(_REFERENCE_AT_LEAST, _TICKS * resolution())
    repetitions = 1
    while cpu_seconds(one_pass, times=repetitions) < at_least and repetitions < _MOST_REPETITIONS:
        repetitions *= 2
    least = min(cpu_seconds(one_pass, times=repetitions) for _ in range(runs))
    return _read(least, f"{repetitions} passes of the reference") / repetitions


def cost(prepare: Callable[[], Callable[[], object]], texts: Iterable[str], *, runs: int = 3) -> float:
    """How many reference passes over ``texts`` (the characters the work reads) one call of the work costs: about
    the same, whatever the host's speed and load, for the same work."""
    chunks = list(texts)
    reference = reference_seconds(chunks, runs=runs)
    return least_cpu_seconds(prepare, runs=runs) / reference


def same(work: Callable[[], object]) -> Callable[[], Callable[[], object]]:
    """``prepare`` for :func:`cost` when the work keeps nothing from one call to the next."""
    return lambda: work
