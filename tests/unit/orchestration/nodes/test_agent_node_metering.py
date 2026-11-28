"""run_agent_node declares each run to the metering proxies, ends it whatever the exec's outcome, and charges it from
what they counted (embry0.execution.run_meter, embry0.agents.run_charge)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from embry0.agents.executor import AgentOutput
from embry0.execution.run_meter import RunMeter
from embry0.orchestration.nodes.agent import run_agent_node
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED
from tests.unit.orchestration.nodes.test_agent_node_provider_fallback import _ProviderRowDb


class _Db(_ProviderRowDb):
    """The provider row, and a settings table with no documents: a charged run reaches the per-job cap's check,
    which reads the budget settings (#682)."""

    def __init__(self) -> None:
        super().__init__([])

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        if "FROM settings_documents" in sql:
            return {"n": 0, "total": 0, "newest": None}
        return await super().fetchrow(sql, *args)

    async def fetch(self, sql: str, *_args: Any) -> list[dict[str, Any]]:
        return []


class _Traces:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.finished: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> str:
        self.created.append(kwargs)
        return str(kwargs.get("trace_id"))

    async def finish(self, trace_id: str, **kwargs: Any) -> None:
        self.finished.append({"trace_id": trace_id, **kwargs})


class _Meter(RunMeter):
    """The orchestrator's meter: the run's calls as the xai-proxy counted them."""

    def __init__(
        self,
        models: dict[str, Any],
        refused_by: dict[str, int] | None = None,
        interrupted: tuple[float, float] = (0.0, 0.0),
    ) -> None:
        self.models = models
        self.refused_by = dict(refused_by or {})
        self.interrupted = interrupted
        self.settled: list[tuple[str, Any, float]] = []
        self.begun: list[tuple[str, str, str | None]] = []
        self.providers: list[str | None] = []
        self.budgets: list[Any] = []
        self.declared_models: list[Any] = []
        self.ended: list[str] = []
        self.charges: list[Any] = []

    async def begin(
        self,
        sandbox_id: str,
        run_id: str,
        job_id: str | None = None,
        provider: str | None = None,
        budget: Any = None,
        models: Any = None,
    ) -> Any:
        from embry0.execution.run_meter import RunWindow

        self.begun.append((sandbox_id, run_id, job_id))
        self.providers.append(provider)
        self.budgets.append(budget)
        self.declared_models.append(models)
        return RunWindow(sandbox_id, run_id, ("xai-proxy",), {"xai-proxy": "b1"})

    async def end(self, window: Any) -> Any:
        # Imported here, so the module loads on a tree without metering and its tests fail on what they assert.
        from embry0.agents.run_charge import BootTally, RunMetering
        from embry0.execution.admin_seq import next_admin_seq

        self.ended.append(window.run_id)
        self.end_numbers = (dict(getattr(window, "end_seqs", {})), next_admin_seq())
        snapshot = {
            "run_id": window.run_id,
            "models": self.models,
            "errors": 0,
            "open": [],
            "budget_refused": sum(self.refused_by.values()),
            "budget_refused_by": self.refused_by,
        }
        return RunMetering(
            run_id=window.run_id,
            targets=("xai-proxy",),
            declared={"xai-proxy": "b1"},
            tallies=(BootTally("xai-proxy", "b1", snapshot, final=True),),
        )

    def charged(self, charge: Any, *, run_id: str) -> None:
        self.charges.append(charge)

    def meters(self, sandbox_id: str) -> bool:
        return True

    def interrupted_spend(self, job_id: str) -> tuple[float, float]:
        return self.interrupted

    async def budgets_ready(self, timeout: float = 30.0) -> bool:
        return True  # what an agent node before #759's last round asked of the meter, so its tests fail on what they assert

    async def settle(self, window: Any, budget: Any, charged_usd: float = 0.0) -> None:
        self.settled.append((window.run_id, budget, charged_usd))


PROXY_COUNT = {"grok-4.7": {"input_tokens": 50_000, "output_tokens": 4_000, "requests": 3, "estimated": 0}}
PROXY_COST = (50_000 * 2.0 + 4_000 * 6.0) / 1e6


async def _run(runner: Any, traces: _Traces, state: dict[str, Any] | None = None, db: Any = None) -> dict[str, Any]:
    return await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "job_id": "job-1", **(state or {})},
        agent_runner=runner,
        agent_type="developer",
        prompt="do it",
        agent_definition={**dict(BUILTIN_SEED["developer"]), "model": "grok-4.7"},
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config={
            "configurable": {
                "db": db if db is not None else _Db(),
                "traces_repo": traces,
                "job_id": "job-1",
                "llm_providers": ("anthropic", "xai"),
            }
        },
    )


def _runner(meter: _Meter | None, run: Any) -> Any:
    class _Runner:
        pass

    runner = _Runner()
    runner.run = run  # type: ignore[attr-defined]
    runner.run_meter = meter  # type: ignore[attr-defined]
    return runner


@pytest.mark.asyncio
async def test_a_grok_run_is_charged_what_its_proxy_counted_and_its_trace_says_how() -> None:
    meter, traces = _Meter(PROXY_COUNT), _Traces()

    async def run(**kwargs: Any) -> AgentOutput:
        assert meter.begun and not meter.ended, "the run was not declared before its exec"
        return AgentOutput(agent_type="developer", output="done", cost_usd=0.001, input_tokens=10, output_tokens=1)

    updates = await _run(_runner(meter, run), traces)
    (sandbox, run_id, job) = meter.begun[0]
    assert (sandbox, job) == ("c1", "job-1") and meter.ended == [run_id]
    assert meter.providers == ["xai"], f"the run was not declared on its record's provider: {meter.providers}"
    finished = traces.finished[0]
    assert finished["cost_usd"] == pytest.approx(PROXY_COST), "not the proxy's count"
    assert (finished["input_tokens"], finished["output_tokens"]) == (50_000, 4_000)
    assert finished["sandbox_cost_usd"] == pytest.approx(0.001)
    assert finished["metering"]["source"] == "proxy"
    assert updates["total_cost_usd"] == pytest.approx(PROXY_COST), "the job's total took the sandbox's figure"
    assert updates["agent_outputs"][0]["cost_usd"] == pytest.approx(PROXY_COST)
    assert [c.source for c in meter.charges] == ["proxy"]


@pytest.mark.asyncio
async def test_a_crashed_exec_is_charged_what_the_proxy_counted() -> None:
    meter, traces = _Meter(PROXY_COUNT), _Traces()

    async def run(**kwargs: Any) -> AgentOutput:
        raise RuntimeError("docker exec died")

    with pytest.raises(RuntimeError):
        await _run(_runner(meter, run), traces)
    assert meter.ended, "a crashed run was not ended on the proxies"
    finished = traces.finished[0]
    assert (finished["result"], finished["cost_usd"]) == ("error", pytest.approx(PROXY_COST)), "charged nothing"
    assert finished["metering"]["source"] == "proxy"


@pytest.mark.asyncio
async def test_a_cancelled_run_is_ended_on_the_proxies_without_waiting() -> None:
    meter, traces = _Meter(PROXY_COUNT), _Traces()
    started = asyncio.Event()

    async def run(**kwargs: Any) -> AgentOutput:
        started.set()
        await asyncio.sleep(60)
        raise AssertionError("not cancelled")

    task = asyncio.create_task(_run(_runner(meter, run), traces))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(50):
        if meter.ended:
            break
        await asyncio.sleep(0.01)
    assert meter.ended == [meter.begun[0][1]], "a cancelled run's declaration was left on the proxies"


@pytest.mark.asyncio
async def test_a_cancelled_run_s_end_is_numbered_before_it_is_handed_over() -> None:
    """The sandbox's next run may be declared before the background end gets to run: the end's numbers are taken when
    the run is cancelled, so they are older than that declaration's, and an end that lands first never has it refused."""
    meter, traces = _Meter(PROXY_COUNT), _Traces()
    started = asyncio.Event()

    async def run(**kwargs: Any) -> AgentOutput:
        started.set()
        await asyncio.sleep(60)
        raise AssertionError("not cancelled")

    task = asyncio.create_task(_run(_runner(meter, run), traces))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(50):
        if meter.ended:
            break
        await asyncio.sleep(0.01)
    numbered, later = meter.end_numbers
    assert set(numbered) == {"xai-proxy"}, "the cancelled run's end was handed over unnumbered"
    assert numbered["xai-proxy"] < later


@pytest.mark.asyncio
async def test_a_run_with_no_meter_is_charged_as_before_and_its_trace_has_no_metering() -> None:
    traces = _Traces()

    async def run(**kwargs: Any) -> AgentOutput:
        return AgentOutput(agent_type="developer", output="done", cost_usd=0.002)

    await _run(_runner(None, run), traces)
    finished = traces.finished[0]
    assert finished["cost_usd"] == pytest.approx(0.002)
    assert "metering" not in finished and "sandbox_cost_usd" not in finished


@pytest.mark.asyncio
async def test_a_meter_that_cannot_begin_or_end_leaves_the_run_charged_its_sandbox_s_figure() -> None:
    class _Broken(_Meter):
        async def begin(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("meter away")

    meter, traces = _Broken(PROXY_COUNT), _Traces()

    async def run(**kwargs: Any) -> AgentOutput:
        return AgentOutput(agent_type="developer", output="done", cost_usd=0.002)

    await _run(_runner(meter, run), traces)
    finished = traces.finished[0]
    assert finished["cost_usd"] == pytest.approx(0.002)
    assert finished["metering"]["source"] == "sandbox"


@pytest.mark.asyncio
async def test_a_runner_whose_every_attribute_is_a_mock_has_no_meter() -> None:
    """A test double's run_meter is not a meter: the run is charged its sandbox's figure, nothing is called."""
    from unittest.mock import AsyncMock

    traces = _Traces()
    runner = AsyncMock()
    runner.run.return_value = AgentOutput(agent_type="developer", output="done", cost_usd=0.002)
    await _run(runner, traces)
    assert traces.finished[0]["cost_usd"] == pytest.approx(0.002)
    assert "metering" not in traces.finished[0]
    runner.run_meter.begin.assert_not_called()


def _done(**kwargs: Any) -> Any:
    async def run(**_kw: Any) -> AgentOutput:
        return AgentOutput(agent_type="developer", output="done", cost_usd=0.001, **kwargs)

    return run


@pytest.mark.asyncio
async def test_a_run_near_its_job_s_cap_is_declared_with_what_the_job_has_left() -> None:
    """$9.90 of the $10 cap spent: the run may spend $0.10, at its record's rates."""
    meter, traces = _Meter(PROXY_COUNT), _Traces()
    await _run(_runner(meter, _done()), traces, state={"total_cost_usd": 9.9})
    (budget,) = meter.budgets
    assert budget is not None and "scopes" in budget, f"the run was declared with no scope a cap: {budget}"
    left = {scope["key"]: scope["remaining_usd"] for scope in budget["scopes"]}
    assert left.get("job:job-1") == pytest.approx(0.1), budget
    assert budget["rates"] == {"in": 2.0, "out": 6.0}, budget


@pytest.mark.asyncio
async def test_a_run_is_held_to_the_deployment_s_day_when_that_leaves_less() -> None:
    class _Spent(_Db):
        async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
            if "date_trunc('day'" in sql:
                return {"daily": 99.95, "monthly": 120.0}
            return await super().fetchrow(sql, *args)

    meter, traces = _Meter(PROXY_COUNT), _Traces()
    await _run(_runner(meter, _done()), traces, db=_Spent())
    (budget,) = meter.budgets
    assert budget is not None and "scopes" in budget, f"the run was declared with no scope a cap: {budget}"
    left = {scope["key"]: scope["remaining_usd"] for scope in budget["scopes"]}
    assert left.get("daily") == pytest.approx(0.05), budget


@pytest.mark.asyncio
async def test_a_run_that_failed_on_a_budget_refusal_says_so_whatever_its_sandbox_read() -> None:
    """An image that predates the code reads the refusal's "$1,429.00" as a rate limit: the job must not wait for one."""
    meter, traces = _Meter(PROXY_COUNT, refused_by={"job": 1}), _Traces()
    said = "xai answered HTTP 402: embry0 budget_exceeded: this call could cost up to $1,429.00 at its ceiling"

    async def run(**_kw: Any) -> AgentOutput:
        return AgentOutput(
            agent_type="developer", is_error=True, error_message=said, error_code="ERR_PROVIDER_RATE_LIMITED"
        )

    updates = await _run(_runner(meter, run), traces)
    finished = traces.finished[0]
    assert finished["error_code"] == "ERR_BUDGET_CALL_REFUSED", f"the run's trace says {finished['error_code']}"
    assert finished["metering"].get("budget_refused") == 1, finished["metering"]
    assert len(meter.begun) == 1, "the run was moved to another model as though its account were spent"
    assert [o.get("error_code") for o in updates["agent_outputs"]] == ["ERR_BUDGET_CALL_REFUSED"]
    assert any("budget_exceeded" in e for e in updates["errors"]), updates["errors"]


def _failed(**kwargs: Any) -> Any:
    async def run(**_kw: Any) -> AgentOutput:
        return AgentOutput(
            agent_type="developer",
            is_error=True,
            error_message="xai answered HTTP 402: embry0 budget_exceeded: this call could cost up to $0.80",
            **kwargs,
        )

    return run


@pytest.mark.asyncio
async def test_a_refusal_for_the_job_s_cap_in_soft_mode_routes_the_job_to_its_budget_pause() -> None:
    from embry0.orchestration.routing.conditions import check_budget

    meter, traces = _Meter(PROXY_COUNT, refused_by={"job": 1}), _Traces()
    # $9.80 spent, and the run's own $0.12: the job stays short of its $10 cap, so only the refusal can stop it.
    state = {"sandbox_container_id": "c1", "job_id": "job-1", "total_cost_usd": 9.8}
    updates = await _run(_runner(meter, _failed()), traces, state=state)
    assert updates["budget_spent_usd"] < 10.0, "not the case under test: the job crossed its cap"
    assert updates.get("budget_refused_cap_usd") == pytest.approx(10.0), (
        f"the refusal was not recorded for the job's routing: {updates.get('budget_refused_cap_usd')}"
    )
    assert check_budget({**state, **updates}) == "over_budget", "the job would run on past a refusal for its cap"


@pytest.mark.asyncio
@pytest.mark.parametrize("run", [_failed(), _done()], ids=["failed", "recovered"])
async def test_a_refusal_for_the_job_s_cap_in_hard_mode_fails_the_job_with_its_code(run: Any) -> None:
    from embry0.safety.error_codes import classify_failure

    meter, traces = _Meter(PROXY_COUNT, refused_by={"job": 1}), _Traces()
    state = {"total_cost_usd": 9.8, "budget_overrun_mode_override": "hard"}  # short of the cap, its run included
    try:
        await _run(_runner(meter, run), traces, state=state)
    except Exception as exc:  # noqa: BLE001 — what the job fails with is the point
        raised = exc
    else:
        pytest.fail("a refusal for the job's cap in hard mode left the job running")
    assert classify_failure(exc=raised).value == "ERR_BUDGET_CALL_REFUSED", f"the job failed with {raised!r}"
    assert getattr(raised, "current_cost", None) == pytest.approx(9.8 + PROXY_COST)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["soft", "hard"])
async def test_a_refusal_for_the_day_s_cap_fails_the_run_and_nothing_more(mode: str) -> None:
    """Crossing the day's cap stops no running job: its refusal fails the run, with its code, in either mode."""

    meter, traces = _Meter(PROXY_COUNT, refused_by={"daily": 1}), _Traces()
    state = {"budget_overrun_mode_override": mode}
    try:
        updates = await _run(_runner(meter, _done()), traces, state=state)
    except Exception as exc:  # noqa: BLE001 — the job must go on
        pytest.fail(f"a refusal for the day's cap ended the job: {exc!r}")
    (output,) = updates["agent_outputs"]
    assert (output["is_error"], output.get("error_code")) == (True, "ERR_BUDGET_CALL_REFUSED"), output
    assert output.get("budget_refused") == "daily", output
    assert updates.get("budget_refused_cap_usd") is None, "a refusal for the day's cap was routed as the job's"
    assert traces.finished[0]["error_code"] == "ERR_BUDGET_CALL_REFUSED"


@pytest.mark.asyncio
async def test_a_run_settles_once_its_trace_holds_its_charge() -> None:
    """Its purses are worked out again from the settled runs, its own now among them (embry0.execution.run_meter)."""
    meter, traces = _Meter(PROXY_COUNT), _Traces()

    async def run(**_kw: Any) -> AgentOutput:
        assert not meter.settled, "the run settled before it ended"
        return AgentOutput(agent_type="developer", output="done", cost_usd=0.001)

    await _run(_runner(meter, run), traces)
    assert traces.finished, "not the case under test: no trace was written"
    assert [run_id for run_id, _, _ in meter.settled] == [meter.begun[0][1]], "the run never left its purses"
    (_, again, charged) = meter.settled[0]
    assert again is not None and {s["key"] for s in again["scopes"]} >= {"job:job-1"}, again
    assert charged == pytest.approx(PROXY_COST), f"the run settled with a charge of {charged}, not its trace's"


@pytest.mark.asyncio
async def test_a_run_declared_before_the_last_process_s_runs_are_closed_has_their_charges_taken_off() -> None:
    """Their charges are not in their traces until they are closed: $0.50 of this job's, $40 of the day's."""
    meter, traces = _Meter(PROXY_COUNT, interrupted=(0.5, 40.0)), _Traces()

    class _Spent(_Db):
        async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
            if "date_trunc('day'" in sql:
                return {"daily": 10.0, "monthly": 10.0}
            return await super().fetchrow(sql, *args)

    await _run(_runner(meter, _done()), traces, state={"total_cost_usd": 1.0}, db=_Spent())
    (budget,) = meter.budgets
    assert budget is not None and "scopes" in budget, f"the run was declared with no scope a cap: {budget}"
    left = {scope["key"]: scope["remaining_usd"] for scope in budget["scopes"]}
    assert left.get("job:job-1") == pytest.approx(10.0 - 1.0 - 0.5), budget
    assert left.get("daily") == pytest.approx(100.0 - 10.0 - 40.0), budget


@pytest.mark.asyncio
async def test_the_sandbox_is_told_whether_its_run_is_held_to_a_budget() -> None:
    """#759: a custom model's calls name an output limit only in a run held to a budget; its record says which."""
    records: list[Any] = []

    async def run(**kwargs: Any) -> AgentOutput:
        records.append(kwargs["config"].get("provider_record"))
        return AgentOutput(agent_type="developer", output="done", cost_usd=0.001)

    class _Unmetered(_Meter):
        def meters(self, sandbox_id: str) -> bool:
            return False  # no proxy meters the run: it is declared with no budget

    budgeted, unmetered = _Meter(PROXY_COUNT), _Unmetered(PROXY_COUNT)
    await _run(_runner(budgeted, run), _Traces())
    await _run(_runner(unmetered, run), _Traces())
    assert budgeted.budgets[0] is not None and unmetered.budgets == [None], (budgeted.budgets, unmetered.budgets)
    marks = [record.get("budgeted") if isinstance(record, dict) else record for record in records]
    assert marks == [True, False], f"the sandbox was told its run's budget as {marks}"


@pytest.mark.asyncio
async def test_a_run_is_declared_with_the_ids_its_model_goes_by() -> None:
    """A call naming another model would be checked and charged at this run's rates: the proxies hold it to these."""
    meter = _Meter(PROXY_COUNT)
    await _run(_runner(meter, _done()), _Traces())
    declared = getattr(meter, "declared_models", None)
    assert declared == [("grok-4.7",)], f"the run was declared with models {declared}"


def test_a_routed_model_is_declared_by_both_its_names() -> None:
    """The chat executor sends the id its provider knows the model by; the CLI and the direct xAI executor, the routed
    one. No record: no hold (an orchestrator's run on a model of no provider)."""
    from types import SimpleNamespace

    try:
        from embry0.orchestration.nodes.agent import run_models
    except ImportError as exc:
        pytest.fail(f"nothing names the ids a run's model goes by: {exc}")
    custom = SimpleNamespace(model="custom/qwen3.8-27b", provider_record=SimpleNamespace(upstream_model="qwen3.8-27b"))
    grok = SimpleNamespace(model="grok-4.7", provider_record=SimpleNamespace(upstream_model="grok-4.7"))
    assert run_models(custom) == ("qwen3.8-27b", "custom/qwen3.8-27b")  # type: ignore[arg-type]
    assert run_models(grok) == ("grok-4.7",)  # type: ignore[arg-type]
    assert run_models(SimpleNamespace(model="m", provider_record=None)) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_run_that_cannot_be_declared_where_its_calls_would_be_refused_is_not_started() -> None:
    """Every call of it there would be refused as outside a run: it fails before it starts, saying why."""
    from embry0.execution.run_meter import RunWindow

    class _Refusing(_Meter):
        async def begin(self, sandbox_id: str, run_id: str, *args: Any, **kwargs: Any) -> Any:
            await super().begin(sandbox_id, run_id, *args, **kwargs)
            try:
                return RunWindow(sandbox_id, run_id, ("xai-proxy",), {}, refusing=("xai-proxy",))  # type: ignore[call-arg]
            except TypeError as exc:
                pytest.fail(f"a run window cannot say where the run would be refused: {exc}")

    started: list[Any] = []

    async def run(**kwargs: Any) -> AgentOutput:
        started.append(kwargs)
        return AgentOutput(agent_type="developer", output="done", cost_usd=0.001)

    meter, traces = _Refusing(PROXY_COUNT), _Traces()
    updates = await _run(_runner(meter, run), traces)
    assert started == [], "the run was started where every call of it would be refused"
    outputs = updates["agent_outputs"]
    assert [(o.get("is_error"), o.get("error_code")) for o in outputs] == [(True, "ERR_RUN_NOT_DECLARED")], outputs
    assert traces.finished and traces.finished[0].get("error_code") == "ERR_RUN_NOT_DECLARED", traces.finished
