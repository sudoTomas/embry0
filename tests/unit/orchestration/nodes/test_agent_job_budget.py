"""Per-job budget cap enforced inside run_agent_node (#246).

`soft` records the overrun and lets the caller's routing stop the pipeline;
`hard` aborts the run at the ceiling with ERR_BUDGET_OVERRUN. The ceiling and
the mode are the settings document's ``budget`` section, read from the store
the graph config carries at every check (#171).
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.execution.agent_runner import AgentOutput
from embry0.orchestration.budget import BudgetOverrunError
from embry0.orchestration.nodes.agent import load_budget_policy, run_agent_node
from embry0.safety.error_codes import ErrorCode, error_code_for_exception
from embry0.settings.sections import CATALOG
from embry0.settings.store import Snapshot, build_snapshot


class _Runner:
    def __init__(self, cost: float) -> None:
        self._cost = cost

    async def run(self, **kwargs: Any) -> AgentOutput:
        return AgentOutput(agent_type="developer", output="done", cost_usd=self._cost)


class _Store:
    """The settings store as a job's graph config carries it: its ``budget`` section is what it was told."""

    def __init__(self, **budget: Any) -> None:
        self.budget = budget
        self.reads = 0

    async def snapshot(self) -> Snapshot:
        self.reads += 1
        row = {"scope": "deployment", "data": {"budget": dict(self.budget)}, "generation": 1, "updated_at": None}
        return build_snapshot(CATALOG, [row], token=(), log=False)


def _config(mode: str, store: _Store | None = None, ceiling: float = 0.0) -> dict[str, Any]:
    store = store or _Store(overrun_mode=mode, max_budget_per_job_usd=ceiling)
    return {"configurable": {"job_id": "J", "settings_store": store}}


async def _run(
    mode: str,
    *,
    spent: float,
    cost: float,
    cap: float = 10.0,
    store: _Store | None = None,
    job_mode: str | None = None,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "sandbox_container_id": "c1",
        "total_cost_usd": spent,
        "pipeline_config": {"budget_usd": cap},
    }
    if job_mode is not None:
        state["budget_overrun_mode_override"] = job_mode
    return await run_agent_node(
        state=state,
        agent_runner=_Runner(cost),
        agent_type="developer",
        prompt="implement",
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config=_config(mode, store),
    )


@pytest.mark.asyncio
async def test_within_cap_records_no_overrun() -> None:
    updates = await _run("hard", spent=1.0, cost=0.5)
    assert updates["total_cost_usd"] == pytest.approx(1.5)
    assert "budget_overrun_usd" not in updates


@pytest.mark.asyncio
async def test_soft_mode_records_the_overrun_and_continues() -> None:
    updates = await _run("soft", spent=9.5, cost=1.0)
    assert updates["total_cost_usd"] == pytest.approx(10.5)
    assert updates["budget_overrun_usd"] == pytest.approx(0.5)
    assert updates["current_stage"] == "developer_complete"


@pytest.mark.asyncio
async def test_hard_mode_aborts_at_the_ceiling() -> None:
    with pytest.raises(BudgetOverrunError) as excinfo:
        await _run("hard", spent=9.5, cost=1.0)
    assert excinfo.value.current_cost == pytest.approx(10.5)
    assert excinfo.value.max_budget == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_a_job_pinned_to_hard_aborts_under_a_soft_fleet_mode() -> None:
    """#275: an unattended job opts itself into the hard stop."""
    with pytest.raises(BudgetOverrunError):
        await _run("soft", spent=9.5, cost=1.0, job_mode="hard")
    with pytest.raises(BudgetOverrunError):
        await _run("soft", spent=9.5, cost=1.0, job_mode="hard", store=_Store(overrun_mode="soft"))


async def _run_watcher_shaped(*, spent: float, cost: float, cap: float, ceiling: float = 0.0) -> dict[str, Any]:
    """State as the watcher's job carries it into the triage turn: the cap
    rides ``max_budget_usd`` and no triage ``pipeline_config`` exists yet."""
    return await run_agent_node(
        state={
            "sandbox_container_id": "c1",
            "total_cost_usd": spent,
            "max_budget_usd": cap,
            "budget_overrun_mode_override": "hard",
        },
        agent_runner=_Runner(cost),
        agent_type="triage",
        prompt="classify",
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config=_config("soft", ceiling=ceiling),
    )


@pytest.mark.asyncio
async def test_a_watcher_shaped_job_stops_at_its_own_cap_before_triage_config_exists() -> None:
    """#275: far under the $10 default, soft fleet mode, no pipeline_config."""
    updates = await _run_watcher_shaped(spent=0.5, cost=1.0, cap=2.0)
    assert "budget_overrun_usd" not in updates
    with pytest.raises(BudgetOverrunError) as excinfo:
        await _run_watcher_shaped(spent=1.5, cost=1.0, cap=2.0)
    assert excinfo.value.max_budget == pytest.approx(2.0)
    assert excinfo.value.current_cost == pytest.approx(2.5)


@pytest.mark.asyncio
async def test_the_lower_of_watcher_cap_and_fleet_ceiling_wins() -> None:
    with pytest.raises(BudgetOverrunError) as excinfo:
        await _run_watcher_shaped(spent=0.75, cost=0.5, cap=2.0, ceiling=1.0)
    assert excinfo.value.max_budget == pytest.approx(1.0)
    with pytest.raises(BudgetOverrunError) as excinfo:
        await _run_watcher_shaped(spent=1.75, cost=0.5, cap=2.0, ceiling=5.0)
    assert excinfo.value.max_budget == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_a_job_cannot_loosen_a_hard_fleet_mode() -> None:
    """The override only tightens: `soft` (or junk) on the job is ignored."""
    for job_mode in ("soft", "off", ""):
        with pytest.raises(BudgetOverrunError):
            await _run("hard", spent=9.5, cost=1.0, job_mode=job_mode)


@pytest.mark.asyncio
async def test_a_junk_job_mode_leaves_a_soft_fleet_mode_soft() -> None:
    updates = await _run("soft", spent=9.5, cost=1.0, job_mode="HARDER")
    assert updates["budget_overrun_usd"] == pytest.approx(0.5)


def test_budget_overrun_error_maps_to_the_error_code() -> None:
    assert error_code_for_exception(BudgetOverrunError(11.0, 10.0)) is ErrorCode.BUDGET_OVERRUN


@pytest.mark.asyncio
async def test_policy_is_the_budget_section_of_the_settings_document() -> None:
    """The section is what the settings document, PATCH /config/settings/budget and the General tab write."""
    ceiling, mode = await load_budget_policy(
        _config("soft", store=_Store(overrun_mode="hard", max_budget_per_job_usd=4.0))
    )
    assert (ceiling, mode) == (4.0, "hard")


@pytest.mark.asyncio
async def test_policy_reads_the_section_at_every_check() -> None:
    """A cap changed while a job runs holds from its next check: nothing is cached across a change."""
    store = _Store(overrun_mode="soft", max_budget_per_job_usd=4.0)
    assert await load_budget_policy(_config("soft", store=store)) == (4.0, "soft")
    store.budget = {"overrun_mode": "hard", "max_budget_per_job_usd": 2.5}
    assert await load_budget_policy(_config("soft", store=store)) == (2.5, "hard")
    assert store.reads == 2


@pytest.mark.asyncio
async def test_policy_without_a_store_is_the_section_defaults() -> None:
    """No store and no DB (tests, legacy callers): the defaults, a $10 ceiling and soft, never no cap."""
    assert await load_budget_policy({"configurable": {}}) == (10.0, "soft")
    assert await load_budget_policy(None) == (10.0, "soft")


@pytest.mark.asyncio
async def test_policy_falls_back_to_the_defaults_when_the_store_cannot_be_read() -> None:
    class _Broken(_Store):
        async def snapshot(self) -> Snapshot:
            raise ConnectionResetError("pool is down")

    assert await load_budget_policy(_config("hard", store=_Broken())) == (10.0, "soft")


@pytest.mark.asyncio
async def test_the_fleet_ceiling_bounds_a_larger_per_job_cap() -> None:
    """An operator lowering max_budget_per_job_usd must outrank triage's pick."""
    with pytest.raises(BudgetOverrunError) as excinfo:
        await _run("hard", spent=3.5, cost=1.0, cap=50.0, store=_Store(overrun_mode="hard", max_budget_per_job_usd=4.0))
    assert excinfo.value.max_budget == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_the_section_alone_drives_the_hard_abort() -> None:
    """End to end: the section's overrun_mode flips enforcement."""
    with pytest.raises(BudgetOverrunError):
        await _run("soft", spent=9.5, cost=1.0, store=_Store(overrun_mode="hard", max_budget_per_job_usd=0.0))


@pytest.mark.asyncio
async def test_runner_error_code_rides_the_output_entry_not_the_state_key() -> None:
    """#304: the code must not land on state["error_code"] — that key is
    terminal for the whole job, and a later retry may well recover."""

    class _StaleRunner:
        async def run(self, **kwargs: Any) -> AgentOutput:
            return AgentOutput(
                agent_type="developer",
                is_error=True,
                error_message="stale image",
                error_code=ErrorCode.SANDBOX_IMAGE_STALE.value,
            )

    updates = await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "pipeline_config": {"budget_usd": 10.0}},
        agent_runner=_StaleRunner(),
        agent_type="developer",
        prompt="implement",
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config=_config("soft"),
    )

    assert updates["agent_outputs"][0]["error_code"] == ErrorCode.SANDBOX_IMAGE_STALE.value
    assert "error_code" not in updates


@pytest.mark.asyncio
async def test_hard_budget_abort_outranks_a_runner_error_code() -> None:
    """Where the two meet (#304 × #322): an errored run that also crosses the
    hard cap aborts with BudgetOverrunError → ERR_BUDGET_OVERRUN. The runner's
    code rides the update dict, which a raise never delivers to state."""

    class _CostlyStalledRunner:
        async def run(self, **kwargs: Any) -> AgentOutput:
            return AgentOutput(
                agent_type="developer",
                is_error=True,
                error_message="exec stalled",
                cost_usd=1.0,
                error_code=ErrorCode.SANDBOX_EXEC_STALLED.value,
            )

    with pytest.raises(BudgetOverrunError) as excinfo:
        await run_agent_node(
            state={"sandbox_container_id": "c1", "total_cost_usd": 9.5, "pipeline_config": {"budget_usd": 10.0}},
            agent_runner=_CostlyStalledRunner(),
            agent_type="developer",
            prompt="implement",
            credentials={"api_key": "", "oauth_token": "oauth-xyz"},
            config=_config("hard"),
        )

    assert error_code_for_exception(excinfo.value) == ErrorCode.BUDGET_OVERRUN


class _Traces:
    """Trace rows of one job: QA sub-task turns land here, never in state (#264)."""

    def __init__(self, already: float) -> None:
        self.sum = already

    async def create(self, **row: Any) -> str:
        return row["trace_id"]  # the running row costs nothing yet

    async def finish(self, trace_id: str, **row: Any) -> None:
        self.sum += row["cost_usd"]

    async def total_cost_for_job(self, job_id: str) -> float:
        return self.sum


async def _run_after_qa(mode: str, *, qa_spend: float) -> dict[str, Any]:
    """A developer turn after a QA round: state holds $1, traces $1 + the QA bill."""
    config = _config(mode)
    config["configurable"]["traces_repo"] = _Traces(1.0 + qa_spend)
    return await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 1.0, "pipeline_config": {"budget_usd": 10.0}},
        agent_runner=_Runner(0.5),
        agent_type="developer",
        prompt="fix",
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config=config,
    )


@pytest.mark.asyncio
async def test_qa_spend_counts_toward_the_cap_in_hard_mode() -> None:
    """#264: $1 in state + $9 of QA in traces + a $0.50 turn crosses a $10 cap."""
    try:
        await _run_after_qa("hard", qa_spend=9.0)
    except BudgetOverrunError as exc:
        assert exc.current_cost == pytest.approx(10.5)
        return
    pytest.fail("a QA-heavy job ran past its hard cap: the cap saw only the $1.50 in state")


@pytest.mark.asyncio
async def test_qa_spend_counts_toward_the_cap_in_soft_mode() -> None:
    updates = await _run_after_qa("soft", qa_spend=9.0)
    assert updates.get("budget_overrun_usd") == pytest.approx(0.5), (
        f"the soft overrun must count the QA bill in traces; updates={updates.get('budget_overrun_usd')}"
    )


@pytest.mark.asyncio
async def test_a_turn_whose_trace_was_not_written_still_counts() -> None:
    """State is the floor: a failed trace write must not lower the total."""
    config = _config("hard")
    traces = _Traces(0.0)
    traces.create = None  # type: ignore[assignment]  # the writes raise; the sum stays $0
    traces.finish = None  # type: ignore[assignment]
    config["configurable"]["traces_repo"] = traces
    with pytest.raises(BudgetOverrunError):
        await run_agent_node(
            state={"sandbox_container_id": "c1", "total_cost_usd": 9.5, "pipeline_config": {"budget_usd": 10.0}},
            agent_runner=_Runner(1.0),
            agent_type="developer",
            prompt="fix",
            credentials={"api_key": "", "oauth_token": "oauth-xyz"},
            config=config,
        )
