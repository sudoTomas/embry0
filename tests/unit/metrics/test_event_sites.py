"""The events are counted where they happen, through an injected LoopMetrics (#285).

No Postgres here: each producer is driven with the fakes its own test module
already uses. The database-backed producers are in ``test_db_sites.py``.
"""

import sys
from unittest.mock import AsyncMock

import httpx
import pytest
from prometheus_client import CollectorRegistry

from embry0.metrics import LoopMetrics
from embry0.orchestration import channels
from embry0.orchestration.channels import PreferNonEmpty
from embry0.services.linear_reconciler import ACTOR, LinearDispatchReconciler, ReconcilerSettings
from tests.unit.cache.test_volume_reaper import _docker, _jobs, _labels, _reaper, _vol
from tests.unit.execution.test_agent_runner import _FINAL_OK, _OLD_RUNNER_USAGE, _proc, _runner_with_procs
from tests.unit.services.test_linear_reconciler import _ctx, _sync, _ticket, _World
from tests.unit.services.test_watcher import _LOKI_PARSE_ERROR, _OK, _run_loop, _service, _validating_service

STACK = ""  # the default watcher_repo in these fixtures


def _metrics() -> tuple[LoopMetrics, CollectorRegistry]:
    registry = CollectorRegistry()
    return LoopMetrics(registry), registry


# ---- watcher -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_recorded_tick_is_counted_with_its_diagnostics() -> None:
    m, registry = _metrics()
    job = {"job_id": "job-w1", "status": "completed", "result_summary": "NO_ISSUE", "total_cost_usd": 0.4}
    svc = _service(lines=["ERROR a", "ERROR b", "ERROR c"], job_result=job)
    svc._metrics = m

    out = await svc.run_once()

    assert out["action"] == "no_issue"
    get = registry.get_sample_value
    assert get("embry0_watcher_ticks_total", {"action": "no_issue", "stack": STACK}) == 1
    assert get("embry0_watcher_analysis_cost_usd_total") == 0.4
    assert get("embry0_watcher_last_analysed_tick_timestamp_seconds", {"stack": STACK}) > 0


@pytest.mark.asyncio
async def test_a_tick_that_reaches_no_verdict_does_not_move_the_analysed_timestamp() -> None:
    m, registry = _metrics()
    # #283: a full draft cap no longer ends the tick before the logs are
    # fetched, so this needs a window to look at to reach skipped_backlog.
    # Without a ledger nothing can be deferred, so the analysis is still not
    # paid for and the tick still reaches no verdict — which is what is
    # being counted here.
    svc = _service(open_proposals=2, lines=["ERROR a", "ERROR b", "ERROR c"])
    svc._metrics = m

    await svc.run_once()

    get = registry.get_sample_value
    assert get("embry0_watcher_ticks_total", {"action": "skipped_backlog", "stack": STACK}) == 1
    assert get("embry0_watcher_last_tick_timestamp_seconds", {"stack": STACK}) > 0
    # #378: per-stack series. A stack that has never analysed has no analysed
    # series at all, which is what the dead-man rule's `> 0` guard expects.
    assert get("embry0_watcher_last_analysed_tick_timestamp_seconds", {"stack": STACK}) is None


@pytest.mark.asyncio
async def test_watcher_health_follows_the_probe() -> None:
    m, registry = _metrics()
    svc = _validating_service(lambda r: httpx.Response(400, text=_LOKI_PARSE_ERROR))
    svc._metrics = m
    await svc.validate_queries()
    assert registry.get_sample_value("embry0_watcher_health", {"state": "invalid_logql", "stack": STACK}) == 1
    assert registry.get_sample_value("embry0_watcher_health", {"state": "ok", "stack": STACK}) == 0


def test_a_new_watcher_reports_unverified() -> None:
    from embry0.services.watcher import WatcherService
    from tests.unit.services.test_watcher import _config

    m, registry = _metrics()
    WatcherService(
        config=_config(),
        jobs_repo=None,
        watcher_runs_repo=None,
        integration_repo=None,
        executor=None,
        filer=None,
        metrics=m,
    )
    assert registry.get_sample_value("embry0_watcher_health", {"state": "unverified", "stack": STACK}) == 1


@pytest.mark.asyncio
async def test_a_blocked_tick_is_counted_because_it_writes_no_row() -> None:
    m, registry = _metrics()
    svc = _service()
    svc._metrics = m
    bad = httpx.Response(400, text=_LOKI_PARSE_ERROR)
    calls = await _run_loop(svc, [bad, bad, bad], iterations=2)
    assert "tick" not in calls
    assert registry.get_sample_value("embry0_watcher_ticks_blocked_total") == 2
    assert _OK  # the healthy-loop case is covered by test_watcher.py


# ---- linear dispatch reconciler --------------------------------------------------


def _reconciler(world: _World, sync: object, m: LoopMetrics, mode: str) -> LinearDispatchReconciler:
    return LinearDispatchReconciler(
        resolve_sync=AsyncMock(return_value=sync),
        resolve_trigger_labels=AsyncMock(return_value={"embry0"}),
        make_context=lambda labels: _ctx(world, labels, None, actor=ACTOR),
        settings=ReconcilerSettings(mode=mode, max_dispatches_per_day=5),
        metrics=m,
    )


@pytest.mark.asyncio
async def test_shadow_sweep_counts_decisions_by_reason() -> None:
    m, registry = _metrics()
    world = _World()
    world.track("ENG-2", status="triaging", job_status="running")
    sync = _sync([_ticket("ENG-1"), _ticket("ENG-2"), _ticket("ENG-3", summary="")])

    await _reconciler(world, sync, m, "shadow").run_once()

    get = registry.get_sample_value
    decisions = "embry0_linear_reconciler_decisions_total"
    assert get(decisions, {"decision": "would_dispatch", "reason": "untracked"}) == 1
    assert get(decisions, {"decision": "skipped", "reason": "dedupe:live_job"}) == 1
    assert get(decisions, {"decision": "skipped", "reason": "unmapped_repo"}) == 1
    assert get("embry0_linear_reconciler_sweeps_total", {"mode": "shadow", "result": "ok"}) == 1
    assert get("embry0_linear_reconciler_last_sweep_tickets", {"decision": "would_dispatch"}) == 1
    assert get("embry0_linear_reconciler_last_sweep_tickets", {"decision": "skipped"}) == 2
    assert get("embry0_linear_reconciler_last_sweep_tickets", {"decision": "listed"}) == 3
    assert get("embry0_linear_reconciler_daily_dispatch_cap") == 5


@pytest.mark.asyncio
async def test_a_standing_decision_is_counted_once_not_once_per_sweep() -> None:
    """At a five-minute interval a stale ticket would otherwise add ~2,016
    increments a week, and increase() would read "288 tickets" where it means
    "one ticket, 288 times"."""
    m, registry = _metrics()
    world = _World()
    world.track("ENG-2", status="triaging", job_status="running")
    sync = _sync([_ticket("ENG-1"), _ticket("ENG-2")])
    reconciler = _reconciler(world, sync, m, "shadow")

    for _ in range(4):
        await reconciler.run_once()

    get = registry.get_sample_value
    decisions = "embry0_linear_reconciler_decisions_total"
    assert get(decisions, {"decision": "would_dispatch", "reason": "untracked"}) == 1
    assert get(decisions, {"decision": "skipped", "reason": "dedupe:live_job"}) == 1
    # The standing view still reports every sweep.
    assert get("embry0_linear_reconciler_last_sweep_tickets", {"decision": "would_dispatch"}) == 1
    assert get("embry0_linear_reconciler_sweeps_total", {"mode": "shadow", "result": "ok"}) == 4


@pytest.mark.asyncio
async def test_a_ticket_whose_decision_changes_is_counted_again() -> None:
    m, registry = _metrics()
    world, sync = _World(), _sync([_ticket("ENG-1")])
    reconciler = _reconciler(world, sync, m, "shadow")

    await reconciler.run_once()
    # A human starts work on it: the same ticket now reads as a skip.
    sync.list_trigger_issues = AsyncMock(return_value=[_ticket("ENG-1", state_type="completed")])  # type: ignore[method-assign]
    await reconciler.run_once()

    decisions = "embry0_linear_reconciler_decisions_total"
    assert registry.get_sample_value(decisions, {"decision": "would_dispatch", "reason": "untracked"}) == 1
    assert registry.get_sample_value(decisions, {"decision": "skipped", "reason": "state:completed"}) == 1


@pytest.mark.asyncio
async def test_a_ticket_that_leaves_and_returns_is_counted_again() -> None:
    m, registry = _metrics()
    world, sync = _World(), _sync([_ticket("ENG-1")])
    reconciler = _reconciler(world, sync, m, "shadow")

    await reconciler.run_once()
    sync.list_trigger_issues = AsyncMock(return_value=[])  # type: ignore[method-assign]
    await reconciler.run_once()
    sync.list_trigger_issues = AsyncMock(return_value=[_ticket("ENG-1")])  # type: ignore[method-assign]
    await reconciler.run_once()

    decisions = "embry0_linear_reconciler_decisions_total"
    assert registry.get_sample_value(decisions, {"decision": "would_dispatch", "reason": "untracked"}) == 2


@pytest.mark.asyncio
async def test_a_reconciler_that_is_off_says_so() -> None:
    """mode=off returns before any sweep, so its sweep gauges stay 0 — every
    reconciler alert has to be guarded by this gauge."""
    m, registry = _metrics()
    await _reconciler(_World(), _sync([]), m, "off").run_once()
    assert registry.get_sample_value("embry0_linear_reconciler_enabled") == 0
    assert registry.get_sample_value("embry0_linear_reconciler_last_sweep_timestamp_seconds") == 0
    _reconciler(_World(), _sync([]), m, "shadow")
    assert registry.get_sample_value("embry0_linear_reconciler_enabled") == 1


@pytest.mark.asyncio
async def test_a_real_dispatch_is_counted_with_why_it_was_due() -> None:
    m, registry = _metrics()
    world, sync = _World(), _sync([_ticket("ENG-1")])

    result = await _reconciler(world, sync, m, "on").run_once()

    assert result["dispatched"] == ["ENG-1"]
    get = registry.get_sample_value
    assert get("embry0_linear_reconciler_decisions_total", {"decision": "dispatched", "reason": "untracked"}) == 1
    assert get("embry0_linear_reconciler_last_sweep_tickets", {"decision": "dispatched"}) == 1


@pytest.mark.asyncio
async def test_a_sweep_with_no_linear_integration_is_counted_as_disabled() -> None:
    m, registry = _metrics()
    await _reconciler(_World(), None, m, "shadow").run_once()
    assert (
        registry.get_sample_value("embry0_linear_reconciler_sweeps_total", {"mode": "shadow", "result": "disabled"})
        == 1
    )


@pytest.mark.asyncio
async def test_a_sweep_that_raises_is_counted_and_still_raises() -> None:
    m, registry = _metrics()
    sync = _sync([])
    sync.list_trigger_issues = AsyncMock(side_effect=RuntimeError("linear 502"))  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await _reconciler(_World(), sync, m, "shadow").run_once()
    assert (
        registry.get_sample_value("embry0_linear_reconciler_sweeps_total", {"mode": "shadow", "result": "error"}) == 1
    )


# ---- runner / infrastructure ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stale_runner_image_is_reported_through_the_hook() -> None:
    seen: list[str] = []
    runner = _runner_with_procs(
        [_proc(returncode=2, stderr=_OLD_RUNNER_USAGE), _proc(returncode=0, stdout=[_FINAL_OK])]
    )
    runner._on_stale_image = seen.append
    await runner.run(container="c", config={"agent_type": "qa", "timeout_seconds": 5, "prompt": "small"})
    assert seen == ["config_stdin_missing"]


def test_agent_runner_stays_importable_inside_the_sandbox_image() -> None:
    """agent_runner.py is baked into the sandbox image, which ships neither
    embry0.metrics nor prometheus_client — it must not import either."""
    import ast
    from pathlib import Path

    import embry0.execution.agent_runner as module

    imported = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(ast.parse(Path(module.__file__).read_text()))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not [name for name in imported if name and ("metrics" in name or "prometheus" in name)]
    assert "embry0.execution.agent_runner" in sys.modules


def test_a_channel_fold_reaches_the_registered_observer() -> None:
    m, registry = _metrics()
    channels.set_fold_observer(m.state_channel_fold)
    try:
        PreferNonEmpty(str, "pr_url").update(["https://x/pull/1", None])
        PreferNonEmpty(str, "pr_url").update(["a lone write"])
    finally:
        channels.set_fold_observer(None)
    PreferNonEmpty(str, "pr_url").update(["x", "y"])  # nobody listening: still folds, counts nothing
    folds = "embry0_state_channel_folded_concurrent_writes_total"
    assert registry.get_sample_value(folds, {"key": "pr_url", "channel": "PreferNonEmpty"}) == 1


@pytest.mark.asyncio
async def test_the_volume_reaper_counts_every_verdict_by_reason() -> None:
    m, registry = _metrics()
    docker = _docker(
        [
            _vol("embry0-qa-vol-job-done", _labels("per-job", "job-done")),
            _vol("embry0-qa-vol-job-live", _labels("per-job", "job-live")),
            _vol("embry0-qa-vol-job-busy", _labels("per-job", "job-busy")),
            _vol("embry0-qa-vol-org-r1", _labels("per-repo", "org/r1")),
        ],
        rm_fails={"embry0-qa-vol-job-busy"},
    )
    reaper = _reaper(docker, _jobs({"job-done": "failed", "job-live": "running", "job-busy": "completed"}), metrics=m)

    await reaper.sweep()

    get = registry.get_sample_value
    volumes = "embry0_volume_reaper_volumes_total"
    assert get(volumes, {"outcome": "removed", "reason": "owner_terminal"}) == 1
    assert get(volumes, {"outcome": "failed", "reason": "owner_terminal"}) == 1
    assert get(volumes, {"outcome": "kept", "reason": "owner_active"}) == 1
    assert get(volumes, {"outcome": "kept", "reason": "shared_scope"}) == 1
    assert get("embry0_volume_reaper_sweeps_total", {"result": "ok"}) == 1


@pytest.mark.asyncio
async def test_a_dry_run_reaper_counts_would_remove() -> None:
    m, registry = _metrics()
    docker = _docker([_vol("embry0-qa-vol-job-done", _labels("per-job", "job-done"))])
    await _reaper(docker, _jobs({"job-done": "failed"}), metrics=m, dry_run=True).sweep()
    volumes = "embry0_volume_reaper_volumes_total"
    assert registry.get_sample_value(volumes, {"outcome": "would_remove", "reason": "owner_terminal"}) == 1


@pytest.mark.asyncio
async def test_a_quiet_tick_reaches_a_verdict_on_the_service_path() -> None:
    # 2026-09-27: the gauge is keyed on the action the service records, so
    # this pins the whole path — a window below watcher_min_log_lines ends
    # the tick as skipped_quiet AND that counts as the loop reaching a verdict.
    m, registry = _metrics()
    svc = _service(lines=["ERROR a"])  # below the fixture's min of 3
    svc._metrics = m

    out = await svc.run_once()

    assert out["action"] == "skipped_quiet"
    get = registry.get_sample_value
    assert get("embry0_watcher_ticks_total", {"action": "skipped_quiet", "stack": STACK}) == 1
    assert get("embry0_watcher_last_verdict_tick_timestamp_seconds", {"stack": STACK}) > 0, (
        "a quiet tick must refresh the dead-man gauge"
    )
    # ... and only that one: nothing was analysed, so that series does not exist.
    assert get("embry0_watcher_last_analysed_tick_timestamp_seconds", {"stack": STACK}) is None
