"""LoopSnapshot — DB-backed gauges that can neither fail nor stall a scrape (#285)."""

import asyncio
import time
from typing import Any

import pytest
from prometheus_client import CollectorRegistry

from embry0.metrics import LoopMetrics
from embry0.metrics.snapshot import LoopSnapshot, SnapshotSource, cases_source, gauge_source, queue_source

pytestmark = pytest.mark.asyncio


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _metrics() -> tuple[LoopMetrics, CollectorRegistry]:
    registry = CollectorRegistry()
    return LoopMetrics(registry), registry


def _counting(value: Any) -> tuple[list[int], Any]:
    calls: list[int] = []

    async def fetch() -> Any:
        calls.append(1)
        return value

    return calls, fetch


async def test_refresh_is_cached_for_the_ttl() -> None:
    m, registry = _metrics()
    clock = Clock()
    calls, fetch = _counting({"pending": 3, "running": 1})
    snapshot = LoopSnapshot(m, {"queue": queue_source(m, fetch)}, ttl_seconds=30, clock=clock)

    await snapshot.refresh()
    await snapshot.refresh()
    assert len(calls) == 1
    assert registry.get_sample_value("embry0_jobs_in_flight", {"status": "pending"}) == 3
    assert registry.get_sample_value("embry0_jobs_in_flight", {"status": "paused"}) == 0

    clock.now += 31
    await snapshot.refresh()
    assert len(calls) == 2


async def test_a_failing_source_is_counted_and_the_rest_still_refresh() -> None:
    m, registry = _metrics()

    async def broken() -> int:
        raise ConnectionError("postgres is gone")

    _, fetch = _counting({"pending": 2})
    snapshot = LoopSnapshot(
        m, {"cap": gauge_source(m.reconciler_daily_dispatches, broken), "queue": queue_source(m, fetch)}
    )
    await snapshot.refresh()  # must not raise
    assert registry.get_sample_value("embry0_loop_snapshot_errors_total", {"source": "cap"}) == 1
    assert registry.get_sample_value("embry0_jobs_in_flight", {"status": "pending"}) == 2
    assert registry.get_sample_value("embry0_loop_snapshot_last_success_timestamp_seconds") == 0


async def test_a_failed_refresh_keeps_the_last_values_and_is_not_retried_within_the_ttl() -> None:
    m, registry = _metrics()
    clock = Clock()
    state = {"fail": False, "calls": 0}

    async def fetch() -> int:
        state["calls"] += 1
        if state["fail"]:
            raise TimeoutError
        return 4

    snapshot = LoopSnapshot(m, {"cap": gauge_source(m.reconciler_daily_dispatches, fetch)}, ttl_seconds=30, clock=clock)
    await snapshot.refresh()
    succeeded_at = registry.get_sample_value("embry0_loop_snapshot_last_success_timestamp_seconds")
    assert succeeded_at and succeeded_at > 0

    state["fail"] = True
    clock.now += 31
    await snapshot.refresh()
    await snapshot.refresh()  # within the TTL of the failure: a sick DB is left alone
    assert state["calls"] == 2
    assert registry.get_sample_value("embry0_linear_reconciler_daily_dispatches") == 4
    assert registry.get_sample_value("embry0_loop_snapshot_last_success_timestamp_seconds") == succeeded_at


async def test_a_hung_query_is_cut_off_at_the_shared_deadline() -> None:
    m, registry = _metrics()
    cancelled: list[bool] = []

    async def hangs() -> int:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return 1

    _, fetch = _counting({"pending": 1})
    snapshot = LoopSnapshot(
        m,
        {"cap": gauge_source(m.reconciler_daily_dispatches, hangs), "queue": queue_source(m, fetch)},
        timeout_seconds=0.05,
    )
    started = time.monotonic()
    await snapshot.refresh()
    assert time.monotonic() - started < 1.0
    assert cancelled == [True]
    assert registry.get_sample_value("embry0_loop_snapshot_errors_total", {"source": "cap"}) == 1
    # The deadline is shared: the source after the hung one had no time left.
    assert registry.get_sample_value("embry0_loop_snapshot_errors_total", {"source": "queue"}) == 1


async def test_without_the_deadline_every_source_is_read_however_long_it_takes() -> None:
    """#421: a caller that must have every gauge (a test of what the sources read) does not race the scrape's
    deadline. A deadline of 0 has run out before the first source: with it nothing is read, without it all is."""
    m, registry = _metrics()
    clock = Clock()
    _, queue = _counting({"pending": 2})
    _, cap = _counting(5)
    snapshot = LoopSnapshot(
        m,
        {"queue": queue_source(m, queue), "cap": gauge_source(m.reconciler_daily_dispatches, cap)},
        timeout_seconds=0,
        clock=clock,
    )

    await snapshot.refresh()
    assert registry.get_sample_value("embry0_jobs_in_flight", {"status": "pending"}) is None, "the deadline holds"

    clock.now += 31
    try:
        await snapshot.refresh(deadline=False)
    except TypeError as exc:
        pytest.fail(f"a refresh cannot be asked to read every source: {exc}")
    read = (
        registry.get_sample_value("embry0_jobs_in_flight", {"status": "pending"}),
        registry.get_sample_value("embry0_linear_reconciler_daily_dispatches"),
    )
    assert read == (2, 5), f"every source is read without the deadline: {read}"
    assert registry.get_sample_value("embry0_loop_snapshot_last_success_timestamp_seconds") > 0


async def test_a_scrape_during_a_refresh_is_served_at_once() -> None:
    m, _ = _metrics()
    release = asyncio.Event()
    calls: list[int] = []

    async def slow() -> int:
        calls.append(1)
        await release.wait()
        return 1

    snapshot = LoopSnapshot(m, {"cap": gauge_source(m.reconciler_daily_dispatches, slow)}, timeout_seconds=5)
    first = asyncio.create_task(snapshot.refresh())
    await asyncio.sleep(0)
    await asyncio.wait_for(snapshot.refresh(), timeout=0.2)  # returns without waiting for `first`
    release.set()
    await first
    assert len(calls) == 1


async def test_cases_source_zeroes_combinations_that_emptied() -> None:
    m, registry = _metrics()
    data: dict[str, Any] = {
        "open": [
            {"stage": "tracked", "origin": "watcher", "count": 12},
            {"stage": "pr_open", "origin": "human", "count": 1},
        ],
        "oldest_age_seconds": {"tracked": 86400.0, "pr_open": 30.0},
        "awaiting_verdict": 2,
        "closed": {"embry0": 3, "out_of_band": 9, "": 1},
    }

    async def fetch() -> dict[str, Any]:
        return data

    source = cases_source(m, fetch)
    source.apply(await source.fetch())
    get = registry.get_sample_value
    assert get("embry0_healing_cases_open", {"stage": "tracked", "origin": "watcher"}) == 12
    assert get("embry0_healing_cases_open", {"stage": "detected", "origin": "ci"}) == 0
    assert get("embry0_healing_oldest_open_case_age_seconds", {"stage": "tracked"}) == 86400
    assert get("embry0_healing_oldest_open_case_age_seconds", {"stage": "merged"}) == 0
    assert get("embry0_healing_cases_awaiting_verdict") == 2
    assert get("embry0_healing_cases_closed", {"resolution_path": "out_of_band"}) == 9
    assert get("embry0_healing_cases_closed", {"resolution_path": "none"}) == 1
    assert get("embry0_healing_cases_closed", {"resolution_path": "cancelled"}) == 0

    data["open"] = []
    data["oldest_age_seconds"] = {}
    source.apply(data)
    assert get("embry0_healing_cases_open", {"stage": "tracked", "origin": "watcher"}) == 0
    assert get("embry0_healing_oldest_open_case_age_seconds", {"stage": "tracked"}) == 0


async def test_an_apply_that_raises_is_a_source_failure_not_a_crash() -> None:
    m, registry = _metrics()

    async def fetch() -> dict[str, Any]:
        return {"unexpected": "shape"}

    snapshot = LoopSnapshot(m, {"cases": SnapshotSource(fetch, cases_source(m, fetch).apply)})
    await snapshot.refresh()
    assert registry.get_sample_value("embry0_loop_snapshot_errors_total", {"source": "cases"}) == 1


async def test_the_repositorys_open_stages_restate_the_lifecycle() -> None:
    from embry0.services.healing.lifecycle import Stage
    from embry0.storage.repositories.healing_cases import OPEN_STAGES

    assert OPEN_STAGES == tuple(stage.value for stage in Stage if stage is not Stage.CLOSED)
