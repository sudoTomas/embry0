"""The measure the matcher bounds use (tests/fixtures/cpu_cost.py) follows the work, not the host.

- It grows with the work: ten times the same work measures about ten times as much, within what load moves it.
- It reads the reference and the work in turns, so load that leaves between them is not read as cheap work.
- It does not count waiting: a thread that sleeps, as one waiting for a CPU on a busy host does, measures nothing
  for it.
- A matcher that backtracks measures thousands of passes of re over its text.
- Each run measures the work as its first call does it.
- A clock too coarse to see the work is refused, not read as zero; a short reference runs until the clock sees it.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable

import pytest

from tests.fixtures.cpu_cost import REFERENCE, ClockTooCoarse, cost, reference_seconds, same

TEXT = "abc/def " * 2000
#: The runs the growth check takes the least of: more than a matcher's bound takes, since it compares two measures.
RUNS = 7
#: How far load moves a measure, measured (#666): with busy loops switching on and off every 1 to 30 ms on both
#: hyperthreads of the measuring CPU's core, 400 runs of the growth check read one pass of the reference at most
#: 1.94 times off one, and ten passes at most 2.15 times off ten. The check allows 3 times either way, and still
#: refuses a measure that does not grow with the work (ten passes read as one) or grows with its square (as 100).
LOAD_SPREAD = 3.0


def test_ten_times_the_work_measures_about_ten_times_as_much() -> None:
    once = cost(same(lambda: REFERENCE.sub("<m>", TEXT)), [TEXT], runs=RUNS)
    ten = cost(same(lambda: [REFERENCE.sub("<m>", TEXT) for _ in range(10)]), [TEXT], runs=RUNS)
    assert 1 / LOAD_SPREAD < once < LOAD_SPREAD, f"the reference measures about one pass of itself: {once:.2f}"
    assert 10 / LOAD_SPREAD < ten / once < 10 * LOAD_SPREAD, f"ten passes measured {ten / once:.1f} times one"


def test_load_that_leaves_between_the_reference_and_the_work_is_not_read_as_cheap_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#666: every reading before the work's first call takes twice as long, as on a CPU shared with a busy loop
    that then stops. Read all at once before the work, the reference read the busy stretch and the work the quiet
    one: one pass of the reference measured half a pass of itself. Read in turns, both have a quiet reading."""
    from tests.fixtures import cpu_cost

    real = cpu_cost.cpu_seconds
    quiet: list[bool] = []

    def loaded(work: Callable[[], object], *, times: int = 1) -> float:
        return real(work, times=times) * (1 if quiet else 2)

    def one_pass() -> None:
        quiet.append(True)
        REFERENCE.sub("<m>", TEXT)

    monkeypatch.setattr(cpu_cost, "cpu_seconds", loaded)
    measured = cost(same(one_pass), [TEXT], runs=RUNS)
    assert measured > 0.75, f"load that left before the work measured one pass of the reference as {measured:.2f}"


def test_waiting_is_not_counted() -> None:
    measured = cost(same(lambda: time.sleep(0.05)), [TEXT])
    assert measured < 1, f"50 ms asleep measured {measured:.2f} passes of re: the measure counts waiting"


def test_a_matcher_that_backtracks_measures_thousands_of_passes() -> None:
    regex = re.compile(r"(a|a)*b")
    text = "a" * 18
    measured = cost(same(lambda: regex.match(text)), [text], runs=1)
    assert measured > 3000, f"a matcher that backtracks measured {measured:.0f} passes of re over its text"


def test_each_run_times_the_work_as_its_first_call_does() -> None:
    """A matcher remembers what it read, so a second call is cheaper: each run prepares the work afresh, untimed."""

    def prepare() -> Callable[[], object]:
        done: list[bool] = []

        def work() -> None:
            if not done:
                REFERENCE.sub("<m>", TEXT * 10)
                done.append(True)

        return work

    measured = cost(prepare, [TEXT])
    assert measured > 5, f"work that costs ten passes on its first call measured {measured:.1f}"


#: A clock that moves in steps of 15.6 ms, as some platforms' thread clocks do.
COARSE = 0.0156


def _coarse(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.fixtures import cpu_cost

    monkeypatch.setattr(cpu_cost, "_now", lambda: math.floor(time.thread_time() / COARSE) * COARSE)
    monkeypatch.setattr(cpu_cost, "resolution", lambda: COARSE)


def test_work_a_coarse_clock_reads_as_no_time_is_refused_naming_the_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bound on work the clock cannot see would pass measuring nothing: the measure refuses it instead."""
    _coarse(monkeypatch)
    with pytest.raises(ClockTooCoarse, match=r"the work read 0 s on time\.thread_time, whose resolution is 0\.0156 s"):
        cost(same(lambda: None), [TEXT])


def test_a_short_reference_on_a_coarse_clock_runs_until_the_clock_reads_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pass over a few characters reads as no time on a coarse clock: it is repeated until it reads ten steps."""
    fine = reference_seconds(["abc"])
    _coarse(monkeypatch)
    coarse = reference_seconds(["abc"])
    assert fine / 3 < coarse < fine * 3, (
        f"a pass over 'abc' read {coarse:.2e} s on a coarse clock, {fine:.2e} s on the fine"
    )


def test_a_clock_that_never_moves_is_refused_for_the_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.fixtures import cpu_cost

    monkeypatch.setattr(cpu_cost, "_now", lambda: 0.0)
    with pytest.raises(ClockTooCoarse, match="passes of the reference read 0 s"):
        reference_seconds(["abc"])


@pytest.mark.parametrize("per_pass", [4e-3, 1e-5], ids=["one-repetition", "many-repetitions"])
def test_the_reference_reads_exactly_what_one_pass_costs(monkeypatch: pytest.MonkeyPatch, per_pass: float) -> None:
    """#671 review: the growth check allows a third to three times one pass, so a reference read at twice its cost (a
    pass counted twice, or half its repetitions) passed every test. On a clock that charges each pass a fixed time,
    the reference reads exactly that time, however many repetitions a reading takes, and one pass of work costs one."""
    from tests.fixtures import cpu_cost

    def charged(work: Callable[[], object], *, times: int = 1) -> float:
        return per_pass * times

    monkeypatch.setattr(cpu_cost, "cpu_seconds", charged)
    assert reference_seconds(["abc"]) == pytest.approx(per_pass, rel=1e-12), "the reference misreads one pass"
    assert cost(same(lambda: None), ["abc"]) == pytest.approx(1.0, rel=1e-12), "a pass of work does not cost one pass"
