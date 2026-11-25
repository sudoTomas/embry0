"""At start-up, every trace the previous process left running is closed and charged what is known of its run
(embry0.services.interrupted_runs, #694)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from embry0.agents.invocation import ProviderRecord
from embry0.execution import events
from embry0.services.interrupted_runs import close_interrupted_runs
from embry0.services.job_events import reset_sequence_memory

XAI = ProviderRecord(
    name="xai",
    runtime="sdk",
    upstream_model="grok-4.7",
    rates=(2.0, 6.0),
    cache_read_factor=0.25,
    cache_write_factor=1.0,
)
PROXY_COST = (50_000 * 2.0 + 4_000 * 6.0) / 1e6


@pytest.fixture(autouse=True)
def _forget_sequences() -> None:
    reset_sequence_memory()


class _Traces:
    def __init__(
        self, rows: list[dict[str, Any]], *, already_closed: set[str] = frozenset(), fail: set[str] = frozenset()
    ):
        self.rows, self.already_closed, self.fail = rows, set(already_closed), set(fail)
        self.closed: dict[str, dict[str, Any]] = {}
        # What the close added to each job's total, and the cost_update it kept: one transaction in the repository.
        self.added: dict[str, float] = {}
        self.updates: dict[str, dict[str, Any]] = {}

    async def running(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows]

    async def close_running(self, trace_id: str, *, cost_update: dict[str, Any] | None = None, **fields: Any) -> bool:
        if trace_id in self.fail:
            raise RuntimeError("database went away")
        if trace_id in self.already_closed:
            return False
        self.closed[trace_id] = fields
        if cost_update is not None:
            job_id = next(str(r["job_id"]) for r in self.rows if r["trace_id"] == trace_id)
            self.added[job_id] = self.added.get(job_id, 0.0) + float(cost_update["cost_usd"])
            self.updates[job_id] = cost_update
        return True


class _Tallies:
    def __init__(self, by_run: dict[str, list[dict[str, Any]]]) -> None:
        self.by_run = by_run

    async def for_run(self, run_id: str) -> list[dict[str, Any]]:
        return list(self.by_run.get(run_id, []))


class _Jobs:
    def __init__(self, by_run: dict[str, list[dict[str, Any]]]) -> None:
        self.by_run = by_run

    async def run_events(self, run_id: str, event_types: Any) -> list[dict[str, Any]]:
        return [e for e in self.by_run.get(run_id, []) if e.get("type") in set(event_types)]


class _Metrics:
    def __init__(self) -> None:
        self.closed: list[tuple[str, str]] = []

    def run_closed(self, cause: str, charged_from: str) -> None:
        self.closed.append((cause, charged_from))

    def __getattr__(self, name: str) -> Any:
        return lambda *a, **k: None


def _trace(run_id: str, job_id: str, *, pricing: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "trace_id": run_id,
        "job_id": job_id,
        "agent_type": "developer",
        "model": "grok-4.7",
        "pricing": pricing if pricing is not None else XAI.trace_pricing(),
        "created_at": datetime.now(UTC) - timedelta(seconds=90),
    }


def _usage(run_id: str, cost: float) -> dict[str, Any]:
    return events.turn_usage(
        input_tokens=1000,
        output_tokens=50,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        cost_usd=cost,
        model="grok-4.7",
        run_id=run_id,
        node="developer",
        agent="developer",
    )


def _kept() -> dict[str, Any]:
    models = {"grok-4.7": {"input_tokens": 50_000, "output_tokens": 4_000, "requests": 3, "estimated": 0}}
    return {"proxy": "xai-proxy", "boot_id": "b1", "final": False, "snapshot": {"models": models, "open": []}}


async def test_each_run_left_running_is_closed_and_charged_what_is_known_of_it() -> None:
    traces = _Traces([_trace("run-metered", "job-1"), _trace("run-sandbox", "job-2"), _trace("run-blank", "job-3")])
    jobs = _Jobs({"run-sandbox": [_usage("run-sandbox", 0.25), {"type": "text", "run_id": "run-sandbox"}]})
    metrics = _Metrics()
    sweep = await close_interrupted_runs(traces, _Tallies({"run-metered": [_kept()]}), jobs, metrics)
    assert (sweep.found, sweep.closed, sweep.left) == (3, 3, [])
    metered, sandbox, blank = (traces.closed[r] for r in ("run-metered", "run-sandbox", "run-blank"))
    assert metered["cost_usd"] == pytest.approx(PROXY_COST) and metered["metering"]["closed"]["charged_from"] == "proxy"
    assert sandbox["cost_usd"] == pytest.approx(0.25) and sandbox["metering"]["closed"]["charged_from"] == "sandbox"
    assert blank["cost_usd"] == 0.0 and blank["metering"]["closed"]["charged_from"] == "nothing"
    for fields in (metered, sandbox, blank):
        assert (fields["result"], fields["error_code"]) == ("error", "ERR_RUN_INTERRUPTED")
        assert fields["metering"]["closed"]["cause"] == "restart"
        assert 80_000 <= fields["duration_ms"] <= 200_000, fields["duration_ms"]
    assert sorted(metrics.closed) == [("restart", "nothing"), ("restart", "proxy"), ("restart", "sandbox")]
    # The charge reaches the job's total as the run's cost_update would have; nothing is added for a $0 close.
    assert traces.added == {"job-1": pytest.approx(PROXY_COST), "job-2": pytest.approx(0.25)}
    assert set(traces.updates) == {"job-1", "job-2"}
    assert (traces.updates["job-2"]["type"], traces.updates["job-2"]["run_id"]) == ("cost_update", "run-sandbox")


async def test_a_run_whose_cost_update_came_is_not_added_to_its_job_twice() -> None:
    update = events.cost_update(
        cost_usd=0.4,
        duration_ms=1,
        duration_api_ms=1,
        num_turns=2,
        tokens_in=10,
        tokens_out=10,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        run_id="run-1",
        node="developer",
        agent="developer",
    )
    traces, jobs = _Traces([_trace("run-1", "job-1")]), _Jobs({"run-1": [update]})
    await close_interrupted_runs(traces, _Tallies({}), jobs, _Metrics())
    assert traces.closed["run-1"]["cost_usd"] == pytest.approx(0.4)
    assert traces.added == {}, "the run's cost was added to its job again"


async def test_a_trace_closed_meanwhile_or_failing_to_close_is_left_and_the_rest_closed() -> None:
    traces = _Traces(
        [_trace("run-gone", "job-1"), _trace("run-fails", "job-2"), _trace("run-ok", "job-3")],
        already_closed={"run-gone"},
        fail={"run-fails"},
    )
    jobs, metrics = _Jobs({r: [_usage(r, 0.1)] for r in ("run-gone", "run-fails", "run-ok")}), _Metrics()
    assert (await close_interrupted_runs(traces, _Tallies({}), jobs, metrics)).closed == 1
    assert set(traces.closed) == {"run-ok"}
    assert metrics.closed == [("restart", "sandbox")], "a trace this did not close was counted"
    assert set(traces.added) == {"job-3"}, "a run this did not close was added to its job"


async def test_a_run_whose_trace_kept_no_pricing_is_charged_its_reported_costs() -> None:
    traces = _Traces([_trace("run-1", "job-1", pricing={})])
    await close_interrupted_runs(traces, None, _Jobs({"run-1": [_usage("run-1", 0.3)]}), None)
    assert traces.closed["run-1"]["cost_usd"] == pytest.approx(0.3)


async def test_a_scan_that_fails_closes_nothing_and_does_not_raise() -> None:
    class _Broken(_Traces):
        async def running(self) -> list[dict[str, Any]]:
            raise RuntimeError("database went away")

    assert (await close_interrupted_runs(_Broken([]), _Tallies({}), _Jobs({}), _Metrics())).closed == 0


async def test_runs_the_budget_does_not_reach_are_left_for_the_background() -> None:
    """A start-up that finds many runs left running goes on after its budget; the rest are closed after, each only
    while still running, and a run this process began meanwhile is never among them."""
    from embry0.services.interrupted_runs import close_runs

    traces = _Traces([_trace(f"run-{n}", f"job-{n}") for n in range(3)])
    jobs = _Jobs({f"run-{n}": [_usage(f"run-{n}", 0.1)] for n in range(3)})
    sweep = await close_interrupted_runs(traces, _Tallies({}), jobs, None, budget_seconds=0)
    assert (sweep.found, sweep.closed, [r["trace_id"] for r in sweep.left]) == (3, 0, ["run-0", "run-1", "run-2"])
    traces.rows.append(_trace("run-new", "job-new"))  # this process's own run, begun after the sweep took its list
    assert await close_runs(sweep.left, traces, _Tallies({}), jobs, None) == 3
    assert set(traces.closed) == {"run-0", "run-1", "run-2"}, "a run of this process was closed"


async def test_the_sweep_reads_where_one_call_ends_and_the_next_begins() -> None:
    """Two real calls with the same figures, a tool_result between them, are two calls (not one)."""
    run = "run-1"
    result = events.tool_result(
        tool_id="t", content="ok", is_error=False, run_id=run, node="developer", agent="developer"
    )
    traces = _Traces([_trace(run, "job-1")])
    await close_interrupted_runs(
        traces, _Tallies({}), _Jobs({run: [_usage(run, 0.25), result, _usage(run, 0.25)]}), None
    )
    closed = traces.closed[run]
    assert (closed["cost_usd"], closed["metering"]["closed"]["calls"]) == (pytest.approx(0.5), 2)
