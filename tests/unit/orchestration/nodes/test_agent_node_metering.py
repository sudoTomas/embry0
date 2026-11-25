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

    def __init__(self, models: dict[str, Any]) -> None:
        self.models = models
        self.begun: list[tuple[str, str, str | None]] = []
        self.ended: list[str] = []
        self.charges: list[Any] = []

    async def begin(self, sandbox_id: str, run_id: str, job_id: str | None = None) -> Any:
        self.begun.append((sandbox_id, run_id, job_id))
        return type("W", (), {"run_id": run_id, "sandbox_id": sandbox_id})()

    async def end(self, window: Any) -> Any:
        # Imported here, so the module loads on a tree without metering and its tests fail on what they assert.
        from embry0.agents.run_charge import BootTally, RunMetering

        self.ended.append(window.run_id)
        snapshot = {"run_id": window.run_id, "models": self.models, "errors": 0, "open": []}
        return RunMetering(
            run_id=window.run_id,
            targets=("xai-proxy",),
            declared={"xai-proxy": "b1"},
            tallies=(BootTally("xai-proxy", "b1", snapshot, final=True),),
        )

    def charged(self, charge: Any, *, run_id: str) -> None:
        self.charges.append(charge)


PROXY_COUNT = {"grok-4.7": {"input_tokens": 50_000, "output_tokens": 4_000, "requests": 3, "estimated": 0}}
PROXY_COST = (50_000 * 2.0 + 4_000 * 6.0) / 1e6


async def _run(runner: Any, traces: _Traces, state: dict[str, Any] | None = None) -> dict[str, Any]:
    return await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "job_id": "job-1", **(state or {})},
        agent_runner=runner,
        agent_type="developer",
        prompt="do it",
        agent_definition={**dict(BUILTIN_SEED["developer"]), "model": "grok-4.7"},
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config={
            "configurable": {
                "db": _Db(),
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
