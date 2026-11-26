"""Auto-accept of watcher drafts (spec 2026-10-25): the bar, the day's cap, and its place in the tick."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from embry0.services.watcher_auto_accept import AutoAcceptBar, auto_accept, why_not
from embry0.services.watcher_stacks import StackConfig, WatcherStack
from embry0.settings.sections import CATALOG
from tests.unit.services.test_watcher import _config, _service

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
BAR = AutoAcceptBar(after_hours=24, min_severity="high", min_occurrences=10, max_per_day=2)


def _case(case_id: str, **over):
    return {
        "id": case_id,
        "origin": "watcher",
        "stage": "tracked",
        "severity": "high",
        "occurrence_count": 40,
        "issue_id": None,
        "tracked_at": NOW - timedelta(hours=30),
        "detected_at": NOW - timedelta(hours=31),
        **over,
    }


@pytest.mark.parametrize(
    ("over", "said"),
    [
        ({}, None),
        ({"severity": "critical"}, None),
        ({"tracked_at": NOW - timedelta(hours=2)}, "grace period"),
        ({"severity": "medium"}, "below high"),
        ({"severity": None}, "unset"),
        ({"occurrence_count": 3}, "fewer than 10"),
        ({"issue_id": "iss-1"}, "retry"),
        ({"stage": "accepted"}, "not a tracked"),
        ({"origin": "human"}, "not a tracked"),
    ],
)
def test_the_bar(over, said):
    reason = why_not(_case("c1", **over), BAR, NOW)
    assert reason is None if said is None else said in reason


async def test_the_oldest_drafts_go_first_up_to_the_day_s_cap_and_answered_ones_never():
    started: list[str] = []

    async def accept(case_id: str) -> str:
        if case_id == "c-refused":
            raise RuntimeError("tracker said no")
        started.append(case_id)
        return f"job-{case_id}"

    candidates = [
        _case("c-new", tracked_at=NOW - timedelta(hours=25)),
        _case("c-declined", tracked_at=NOW - timedelta(hours=50)),
        _case("c-refused", tracked_at=NOW - timedelta(hours=49)),
        _case("c-old", tracked_at=NOW - timedelta(hours=48)),
        _case("c-low", severity="low"),
    ]
    done = await auto_accept(
        candidates=candidates,
        bar=BAR,
        accepted_today=0,
        answered=lambda proposal_id: proposal_id == "case:c-declined",
        accept=accept,
        now=NOW,
    )
    assert done == [{"case_id": "c-old", "job_id": "job-c-old"}, {"case_id": "c-new", "job_id": "job-c-new"}]
    assert started == ["c-old", "c-new"]

    started.clear()
    one_left = await auto_accept(
        candidates=candidates, bar=BAR, accepted_today=1, answered=lambda _: False, accept=accept, now=NOW
    )
    assert [d["case_id"] for d in one_left] == ["c-declined"], "one slot left today, the oldest draft takes it"


def _section(**values):
    """A reader of the settings document's watcher section holding ``values``, as production's reads it."""
    section = CATALOG.get("watcher")
    if section is None:
        pytest.fail("the settings document has no watcher section: auto-accept is read from the environment")
    model = section.validate(values)

    async def read():
        return model

    return read


def _stack(**settings):
    return StackConfig(_config(), WatcherStack(repo="owner/repo", logql='{app="x"}', settings=settings))


@pytest.mark.asyncio
async def test_the_bar_is_the_stack_s_own_values_over_the_section():
    svc = _service(
        config=_stack(auto_accept=True, auto_accept_min_severity="Medium", auto_accept_max_per_day=5),
        lines=[],
    )
    svc._watcher_settings = _section(
        auto_accept_after_hours=6, auto_accept_min_occurrences=3, auto_accept_max_per_day=9
    )
    svc._auto_acceptor = AsyncMock(return_value=[])

    await svc.run_once()
    _, bar = svc._auto_acceptor.await_args.args
    assert bar == AutoAcceptBar(after_hours=6.0, min_severity="medium", min_occurrences=3, max_per_day=5), (
        "each key the stack sets is its own; every other is the deployment's section"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("section", "stack", "asked"),
    [
        ({"auto_accept": True}, {}, True),
        ({}, {}, False),
        ({}, {"auto_accept": True}, True),
        ({"auto_accept": True}, {"auto_accept": False}, False),
    ],
)
async def test_the_tick_asks_only_a_stack_that_turned_it_on(section, stack, asked):
    svc = _service(config=_stack(**stack), lines=[])
    svc._watcher_settings = _section(**section)
    order: list[str] = []
    svc._reconcile_drafts = AsyncMock(side_effect=lambda *_: order.append("reconcile"))  # type: ignore[method-assign]
    svc._auto_acceptor = AsyncMock(side_effect=lambda *_: order.append("accept") or [])

    await svc.run_once()
    assert svc._auto_acceptor.await_count == (1 if asked else 0)
    if asked:
        assert order == ["reconcile", "accept"], (
            "a draft cancelled since the last tick is closed before the bar is asked"
        )
        repo, bar = svc._auto_acceptor.await_args.args
        assert repo == "owner/repo" and isinstance(bar, AutoAcceptBar)


@pytest.mark.asyncio
async def test_a_failing_acceptor_never_costs_the_tick():
    svc = _service(config=_stack(auto_accept=True), lines=[])
    svc._auto_acceptor = AsyncMock(side_effect=RuntimeError("ledger down"))

    out = await svc.run_once()
    assert out["action"] == "skipped_quiet"
