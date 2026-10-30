"""Soft-mode routing stops a continued job at the cap the agent node enforces (#283, #171 review).

After a person continued a job past its cap, soft-mode routing compared the
spend with triage's cap plus the allowance, not with the cap the agent node
enforces. A job whose triage cap was above the fleet ceiling could spend past
what its pause promised, and making the ceiling a hard limit
(``budget.continue_may_exceed_ceiling: false``) or lowering it did not hold for
a job already continued. The node records the cap in force; routing decides
on it.

Each case runs the real agent node over a settings store holding the budget
section, then the routing condition the developer node calls on its updates.
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.execution.agent_runner import AgentOutput
from embry0.orchestration.budget import allowance_to_continue, resolve_job_cap
from embry0.orchestration.nodes.agent import run_agent_node
from embry0.orchestration.routing.conditions import check_budget
from embry0.settings.sections import CATALOG
from embry0.settings.store import Snapshot, build_snapshot


class _Runner:
    def __init__(self, cost: float) -> None:
        self._cost = cost

    async def run(self, **kwargs: Any) -> AgentOutput:
        return AgentOutput(agent_type="developer", output="done", cost_usd=self._cost)


class _Store:
    def __init__(self, **budget: Any) -> None:
        self.budget = budget

    async def snapshot(self) -> Snapshot:
        row = {"scope": "deployment", "data": {"budget": dict(self.budget)}, "generation": 1, "updated_at": None}
        return build_snapshot(CATALOG, [row], token=(), log=False)


async def _turn(state: dict[str, Any], store: _Store, cost: float) -> dict[str, Any]:
    """One agent turn in soft mode, and what the developer node's routing then sees."""
    updates = await run_agent_node(
        state={"sandbox_container_id": "c1", **state},
        agent_runner=_Runner(cost),
        agent_type="developer",
        prompt="implement",
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config={"configurable": {"job_id": "J", "settings_store": store}},
    )
    return {**state, **updates}


def _continued(triage_cap: float, spent: float, ceiling: float, extra: float | None = None) -> dict[str, Any]:
    """A job a person continued from its budget pause while a continue could pass the ceiling (the default)."""
    state = {
        "pipeline_config": {"budget_usd": triage_cap},
        "total_cost_usd": spent,
        "budget_overrun_usd": spent - triage_cap,
    }
    return {**state, "budget_allowance_usd": allowance_to_continue(state, ceiling=ceiling, extra=extra)}


@pytest.mark.asyncio
async def test_a_continued_job_stops_at_the_ceiling_once_it_is_made_a_hard_limit() -> None:
    """The reviewer's case: triage cap 5 under a ceiling of 10, continued at $5.20 with $50, then the
    operator sets continue_may_exceed_ceiling to false. The job must stop at 10, not run to 55."""
    state = _continued(5.0, 5.2, 10.0, extra=50.0)
    assert resolve_job_cap(state, ceiling=10.0) == pytest.approx(55.2), "the continue as granted"
    flipped = _Store(overrun_mode="soft", max_budget_per_job_usd=10.0, continue_may_exceed_ceiling=False)

    under = await _turn({**state, "total_cost_usd": 9.0}, flipped, 0.5)
    assert check_budget(under) == "within_budget", "under the clamped cap the job goes on"
    over = await _turn({**state, "total_cost_usd": 10.0}, flipped, 0.5)
    assert over.get("budget_cap_usd") == pytest.approx(10.0), "the node records the cap it enforced"
    assert check_budget(over) == "over_budget", (
        "a continued job ran past a ceiling the operator made a hard limit: routing used triage's cap plus the allowance"
    )


@pytest.mark.asyncio
async def test_a_continued_job_stops_at_the_cap_its_pause_promised_when_triage_picked_more_than_the_ceiling() -> None:
    """#283: triage cap 20 under a ceiling of 10; the pause promised one more cap, to 20.50."""
    state = _continued(20.0, 10.5, 10.0)
    store = _Store(overrun_mode="soft", max_budget_per_job_usd=10.0)
    within = await _turn({**state, "total_cost_usd": 19.5}, store, 0.5)
    assert check_budget(within) == "within_budget"
    over = await _turn({**state, "total_cost_usd": 20.5}, store, 0.5)
    assert over.get("budget_cap_usd") == pytest.approx(20.5), "the node records the cap it enforced"
    assert check_budget(over) == "over_budget", "the job ran past 20.50 toward triage's 20 plus the allowance, 30.50"


@pytest.mark.asyncio
async def test_a_lowered_ceiling_holds_for_a_job_already_continued() -> None:
    """Triage cap 8 under a ceiling of 10, continued at $8.40 (one more cap, to 16.40). The operator then
    lowers the ceiling to 3: the cap in force is 3 plus the allowance, 11.40, not triage's 8 plus it."""
    state = _continued(8.0, 8.4, 10.0)
    store = _Store(overrun_mode="soft", max_budget_per_job_usd=3.0)
    assert check_budget(await _turn({**state, "total_cost_usd": 10.5}, store, 0.5)) == "within_budget"
    over = await _turn({**state, "total_cost_usd": 11.5}, store, 0.5)
    assert over.get("budget_cap_usd") == pytest.approx(11.4), "the node records the cap it enforced"
    assert check_budget(over) == "over_budget", "the lowered ceiling did not hold for a job already continued"


def test_a_state_no_node_checked_since_the_upgrade_routes_as_before() -> None:
    """A job paused before this release has no recorded cap: its overrun verdict and allowance decide."""
    assert check_budget(
        {"pipeline_config": {"budget_usd": 10.0}, "total_cost_usd": 11.0, "budget_overrun_usd": 1.0}
    ) == ("over_budget")
    raised = {
        "pipeline_config": {"budget_usd": 10.0},
        "total_cost_usd": 11.0,
        "budget_overrun_usd": 1.0,
        "budget_allowance_usd": 11.0,
    }
    assert check_budget(raised) == "within_budget"
