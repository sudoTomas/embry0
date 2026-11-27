"""What an agent run may still spend, as its declaration at the metering proxies carries it
(embry0.orchestration.run_budget)."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.agents.invocation import ProviderRecord


def _record(rates: tuple[float, float] | None = (2.0, 6.0)) -> ProviderRecord:
    return ProviderRecord(
        name="xai",
        runtime="sdk",
        upstream_model="grok-4.7",
        rates=rates,
        cache_read_factor=0.25,
        cache_write_factor=1.5,
        resolved_at="2026-11-27T00:00:00+00:00",
    )


def _budget(record: Any, limits: list[tuple[str, float, float]]) -> Any:
    try:
        from embry0.orchestration.run_budget import Limit, run_budget
    except ImportError as exc:
        pytest.fail(f"no run is given a budget: {exc}")
    return run_budget(record, [Limit(name, cap, spent) for name, cap, spent in limits])


def test_a_run_may_spend_the_least_any_cap_has_left_at_its_rates() -> None:
    budget = _budget(_record(), [("job", 10.0, 9.9), ("daily", 100.0, 40.0), ("monthly", 500.0, 450.0)])
    assert budget == {
        "remaining_usd": pytest.approx(0.1),
        "limit": "job",
        "rates": {"in": 2.0, "out": 6.0},
        "cache": {"read": 0.25, "write": 1.5},
    }, budget


def test_the_day_s_cap_can_leave_the_least() -> None:
    budget = _budget(_record(), [("job", 10.0, 1.0), ("daily", 100.0, 99.5), ("monthly", 500.0, 0.0)])
    assert (budget["remaining_usd"], budget["limit"]) == (pytest.approx(0.5), "daily"), budget


def test_a_cap_already_passed_leaves_nothing_not_less() -> None:
    budget = _budget(_record(), [("job", 10.0, 12.5)])
    assert (budget["remaining_usd"], budget["limit"]) == (0.0, "job"), budget


def test_a_cap_of_0_is_no_cap() -> None:
    """As the budget section has it: 0 sets none."""
    budget = _budget(_record(), [("job", 10.0, 2.0), ("daily", 0.0, 50.0), ("monthly", 0.0, 50.0)])
    assert (budget["remaining_usd"], budget["limit"]) == (pytest.approx(8.0), "job"), budget


@pytest.mark.parametrize(
    ("record", "limits"),
    [
        (_record(rates=None), [("job", 10.0, 9.9)]),
        (_record(rates=(0.0, 0.0)), [("job", 10.0, 9.9)]),
        (None, [("job", 10.0, 9.9)]),
        (_record(), [("job", 0.0, 9.9), ("daily", 0.0, 1.0)]),
        (_record(), []),
    ],
    ids=["unpriced", "free", "no-record", "no-cap-set", "no-limits"],
)
def test_a_free_run_or_one_with_no_cap_is_given_no_budget(record: Any, limits: list[tuple[str, float, float]]) -> None:
    assert _budget(record, limits) is None


def test_a_spend_that_leaves_no_finite_amount_is_no_figure_to_hold_a_run_to() -> None:
    """A proxy refuses a declaration naming one, which would leave the run undeclared."""
    budget = _budget(_record(), [("job", 10.0, float("-inf")), ("daily", 100.0, 20.0)])
    assert (budget["remaining_usd"], budget["limit"]) == (pytest.approx(80.0), "daily"), budget
    assert _budget(_record(), [("job", 10.0, float("-inf"))]) is None
