"""A run that never ends normally is never left running at $0: a cancelled run's trace is closed at the cancel, and a
crashed run is charged its sandbox's last figure (embry0.agents.run_close, #694)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from embry0.execution import events
from embry0.orchestration.nodes import agent as agent_module
from embry0.orchestration.nodes.agent import run_agent_node
from embry0.services.job_events import reset_sequence_memory
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED
from tests.unit.orchestration.nodes.test_agent_node_metering import (
    PROXY_COST,
    PROXY_COUNT,
    _Db,
    _Meter,
    _runner,
)
from tests.unit.orchestration.nodes.test_agent_node_metering import _Traces as _MeteringTraces

#: grok-4.7 at the xAI record's rates ($2 / $6 per million).
CALL_A, CALL_B = (1000 * 2.0 + 30 * 6.0) / 1e6, (1400 * 2.0 + 60 * 6.0) / 1e6


@pytest.fixture(autouse=True)
def _forget_sequences() -> None:
    reset_sequence_memory()


class _Traces(_MeteringTraces):
    """The traces repository: a close only while the row is still running, with the cost_update it adds."""

    async def close_running(self, trace_id: str, *, cost_update: dict[str, Any] | None = None, **fields: Any) -> bool:
        if any(f["trace_id"] == trace_id for f in self.finished):
            return False
        self.finished.append({"trace_id": trace_id, **fields, "cost_update": cost_update})
        return True


class _Jobs:
    def __init__(self) -> None:
        self.appended: list[dict[str, Any]] = []
        self.added = 0.0

    async def append_log_event(self, job_id: str, event: dict[str, Any]) -> int:
        self.appended.append(event)
        return len(self.appended)

    async def increment_cost(self, job_id: str, cost_delta: float) -> None:
        self.added += cost_delta

    async def get(self, job_id: str) -> dict[str, Any] | None:
        return {"job_id": job_id}


class _Metrics:
    def __init__(self) -> None:
        self.closed: list[tuple[str, str]] = []

    def run_closed(self, cause: str, charged_from: str) -> None:
        self.closed.append((cause, charged_from))

    def __getattr__(self, name: str) -> Any:
        return lambda *a, **k: None


class _Kept:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def for_run(self, run_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows]


def _usages(run_id: str) -> list[dict[str, Any]]:
    """Two model calls; the first sent again with its second content block, as the CLI does."""

    def usage(input_tokens: int, output_tokens: int, cost: float | None) -> dict[str, Any]:
        return events.turn_usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost_usd=cost,
            model="grok-4.7",
            run_id=run_id,
            node="developer",
            agent="developer",
        )

    return [usage(1000, 10, None), usage(1000, 30, None), usage(1400, 60, None)]


async def _run(runner: Any, traces: _Traces, jobs: _Jobs, metrics: _Metrics) -> dict[str, Any]:
    return await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "job_id": "job-1"},
        agent_runner=runner,
        agent_type="developer",
        prompt="do it",
        agent_definition={**dict(BUILTIN_SEED["developer"]), "model": "grok-4.7"},
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config={
            "configurable": {
                "db": _Db(),
                "traces_repo": traces,
                "jobs_repo": jobs,
                "loop_metrics": metrics,
                "job_id": "job-1",
                "llm_providers": ("anthropic", "xai"),
            }
        },
    )


async def _cancel_mid_run(
    meter: Any, usages: bool = True, traces: _Traces | None = None
) -> tuple[_Traces, _Jobs, _Metrics]:
    traces, jobs, metrics = traces or _Traces(), _Jobs(), _Metrics()
    started = asyncio.Event()

    async def run(**kwargs: Any) -> Any:
        for event in _usages(kwargs["config"]["run_id"]) if usages else []:
            kwargs["on_event"](event)
        started.set()
        await asyncio.sleep(60)
        raise AssertionError("not cancelled")

    task = asyncio.create_task(_run(_runner(meter, run), traces, jobs, metrics))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    return traces, jobs, metrics


async def test_a_cancelled_run_is_closed_and_charged_its_sandbox_s_last_figure() -> None:
    traces, jobs, metrics = await _cancel_mid_run(None)
    assert len(traces.finished) == 1, "the cancelled run's trace was left running"
    closed = traces.finished[0]
    assert (closed["result"], closed["error_code"]) == ("error", "ERR_RUN_INTERRUPTED")
    assert closed["cost_usd"] == pytest.approx(CALL_A + CALL_B), "each model call is charged once"
    assert (closed["input_tokens"], closed["output_tokens"]) == (2400, 90)
    assert closed["metering"]["closed"] == {
        "cause": "cancelled",
        "charged_from": "sandbox",
        "sandbox_figure_from": "turn_usage",
        "calls": 2,
        "unpriced_calls": 0,
        "reason": closed["metering"]["closed"]["reason"],
    }
    assert metrics.closed == [("cancelled", "sandbox")]
    update = closed["cost_update"]
    assert update is not None, "the charge did not reach the job's total"
    assert (update["type"], update["cost_usd"]) == ("cost_update", pytest.approx(CALL_A + CALL_B))


async def test_a_cancelled_metered_run_is_charged_the_tallies_kept_of_it() -> None:
    meter = _Meter(PROXY_COUNT)
    meter._repository = _Kept(  # type: ignore[assignment]
        [{"proxy": "xai-proxy", "boot_id": "b1", "final": False, "snapshot": {"models": PROXY_COUNT, "open": []}}]
    )
    meter.charged = lambda *a, **k: None  # type: ignore[method-assign]
    traces, _jobs, metrics = await _cancel_mid_run(meter)
    assert traces.finished, "the cancelled run's trace was left running"
    closed = traces.finished[0]
    assert closed["cost_usd"] == pytest.approx(max(PROXY_COST, CALL_A + CALL_B))
    assert closed["metering"]["closed"]["charged_from"] == "proxy"
    assert closed["metering"]["gap"] is True, "a run whose end was never read was taken as whole"
    assert metrics.closed == [("cancelled", "proxy")]


async def test_a_cancelled_run_with_nothing_known_is_closed_at_zero_and_says_so() -> None:
    traces, jobs, metrics = await _cancel_mid_run(None, usages=False)
    assert traces.finished, "the cancelled run's trace was left running"
    closed = traces.finished[0]
    assert (closed["cost_usd"], closed["metering"]["closed"]["charged_from"]) == (0.0, "nothing")
    assert metrics.closed == [("cancelled", "nothing")]
    assert closed["cost_update"] is None, "a $0 close added to the job's total"


async def test_a_crashed_run_is_charged_its_sandbox_s_last_figure() -> None:
    traces, jobs, metrics = _Traces(), _Jobs(), _Metrics()

    async def run(**kwargs: Any) -> Any:
        for event in _usages(kwargs["config"]["run_id"]):
            kwargs["on_event"](event)
        raise RuntimeError("the runner lost the sandbox")

    with pytest.raises(RuntimeError):
        await _run(_runner(None, run), traces, jobs, metrics)
    crashed = traces.finished[0]
    assert crashed["result"] == "error"
    assert crashed["cost_usd"] == pytest.approx(CALL_A + CALL_B), "a crashed run was charged $0"
    assert crashed["input_tokens"] == 2400


async def test_a_slow_close_does_not_hold_the_cancellation_up(monkeypatch: Any) -> None:
    monkeypatch.setattr(agent_module, "CANCEL_CLOSE_WAIT_SECONDS", 0.1)

    class _SlowTraces(_Traces):
        async def close_running(self, trace_id: str, **kwargs: Any) -> bool:
            await asyncio.sleep(0.6)
            return await super().close_running(trace_id, **kwargs)

    traces = _SlowTraces()
    started = time.monotonic()
    await _cancel_mid_run(None, traces=traces)
    assert time.monotonic() - started < 0.5, "the cancellation waited for the close past its bound"
    assert traces.finished == []
    await asyncio.gather(*list(agent_module._BACKGROUND_CLOSES))
    assert len(traces.finished) == 1, "the close did not go on by itself"


async def test_a_cancel_while_the_run_is_ended_on_the_proxies_closes_its_trace() -> None:
    """The review's NIT 1: the exec returned, and the cancel comes while the run is ended on the proxies."""
    entered = asyncio.Event()

    class _SlowEnd(_Meter):
        async def end(self, window: Any) -> Any:
            entered.set()
            await asyncio.sleep(60)

    meter = _SlowEnd(PROXY_COUNT)
    meter._repository = _Kept([])  # type: ignore[assignment]
    traces, jobs, metrics = _Traces(), _Jobs(), _Metrics()

    async def run(**kwargs: Any) -> Any:
        for event in _usages(kwargs["config"]["run_id"]):
            kwargs["on_event"](event)
        from embry0.agents.executor import AgentOutput

        return AgentOutput(agent_type="developer", output="done", cost_usd=CALL_A + CALL_B)

    task = asyncio.create_task(_run(_runner(meter, run), traces, jobs, metrics))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert traces.finished, "a cancel after the exec returned left the trace running"
    closed = traces.finished[0]
    assert closed["cost_usd"] == pytest.approx(CALL_A + CALL_B)
    # The run finished and its result was in hand: the trace says how the run went, not that it was interrupted.
    assert (closed["result"], closed["error_code"], closed["result_summary"]) == ("success", None, "done")


async def test_a_cancel_while_the_local_slot_is_released_closes_the_trace(monkeypatch: Any) -> None:
    """The exec returned; the cancel comes while the run's slot on the local model is given back."""
    from tests.fixtures.provider_catalogue import enable_providers
    from tests.unit.orchestration.nodes.test_agent_node_local_models import _LOCAL, _Slots
    from tests.unit.orchestration.nodes.test_agent_node_local_models import _run as _run_local

    enable_providers(monkeypatch, "custom")
    entered = asyncio.Event()

    class _SlowRelease(_Slots):
        async def release(self, lease_id: str) -> None:
            entered.set()
            await asyncio.sleep(60)

    traces = _Traces()
    task = asyncio.create_task(
        _run_local(slots=_SlowRelease(), state={"local_first_model": _LOCAL}, configurable={"traces_repo": traces})
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert traces.finished, "a cancel while the slot was given back left the trace running"
    closed = traces.finished[0]
    assert closed["metering"]["closed"]["cause"] == "cancelled"
    assert (closed["result"], closed["error_code"]) == ("success", None), "a finished run was labelled interrupted"


async def test_a_cancel_while_the_run_s_own_figures_are_written_does_not_lose_them() -> None:
    entered = asyncio.Event()

    class _SlowFinish(_Traces):
        async def finish(self, trace_id: str, **kwargs: Any) -> None:
            entered.set()
            await asyncio.sleep(0.3)
            await super().finish(trace_id, **kwargs)

    traces, jobs, metrics = _SlowFinish(), _Jobs(), _Metrics()

    async def run(**kwargs: Any) -> Any:
        from embry0.agents.executor import AgentOutput

        return AgentOutput(agent_type="developer", output="done", cost_usd=0.5)

    task = asyncio.create_task(_run(_runner(None, run), traces, jobs, metrics))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.gather(*list(agent_module._SHIELDED_WRITES))
    assert [(f["result"], f["cost_usd"]) for f in traces.finished] == [("success", 0.5)], traces.finished


async def test_a_cancelled_run_s_calls_with_the_same_figures_are_each_charged() -> None:
    traces, jobs, metrics = _Traces(), _Jobs(), _Metrics()
    started = asyncio.Event()

    async def run(**kwargs: Any) -> Any:
        run_id = kwargs["config"]["run_id"]
        call = _usages(run_id)[2]
        result = events.tool_result(
            tool_id="t", content="x" * 100, is_error=False, run_id=run_id, node="developer", agent="developer"
        )
        for event in (call, result, call):
            kwargs["on_event"](event)
        started.set()
        await asyncio.sleep(60)

    task = asyncio.create_task(_run(_runner(None, run), traces, jobs, metrics))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    closed = traces.finished[0]
    assert (closed["cost_usd"], closed["metering"]["closed"]["calls"]) == (pytest.approx(2 * CALL_B), 2)
