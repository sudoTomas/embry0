"""A watched stack's changes apply without a restart (#746).

Each watcher reads its stack's row before every tick, so a changed setting
applies to the next tick and a stack disabled or deleted ticks no more. The
fleet looks at the table and starts a watcher for each stack enabled and not
running, and stops the watcher of each one no longer enabled: its metrics go.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import importlib
import pathlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from prometheus_client import CollectorRegistry

import embry0.api.app as app_module
from embry0.metrics.loop import WATCHER_HEALTH_STATES, LoopMetrics
from embry0.services.logql_check import LogQLCheck, LogQLStatus
from embry0.services.watcher_stacks import StackConfig, WatcherStack
from tests.unit.services.test_watcher import _config, _loki, _service

STACK = WatcherStack(repo="acme/web", logql='{app="web"}', tracker="github", settings={"auto_accept": True})


def _fleet_module() -> Any:
    try:
        return importlib.import_module("embry0.services.watcher_fleet")
    except ImportError:
        pytest.fail("there is no watcher fleet: a stack added, disabled or deleted after startup waits for a restart")


# ---- each watcher reads its row ---------------------------------------------------------------------------------


def _watcher(stored: list[WatcherStack | None]) -> Any:
    """A watcher of :data:`STACK` whose row reads as ``stored``, in turn, and whose queries Loki found valid."""
    svc = _service(config=StackConfig(_config(), STACK), lines=[])
    svc._stack_reader = AsyncMock(side_effect=stored)
    svc._query_checks = {"WATCHER_LOGQL": LogQLCheck(LogQLStatus.OK)}
    svc._auto_acceptor = AsyncMock(return_value=[])
    svc.run_once = AsyncMock(wraps=svc.run_once)
    return svc


@pytest.mark.asyncio
async def test_a_stack_disabled_since_its_last_tick_ticks_no_more() -> None:
    for stored in (dataclasses.replace(STACK, enabled=False), None):
        svc = _watcher([stored])
        await svc._iterate()
        assert svc.run_once.await_count == 0, f"a stack {'deleted' if stored is None else 'disabled'} still ticked"
        svc._runs.record_tick_attempt.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_changed_row_setting_applies_to_the_next_tick() -> None:
    """``auto_accept: false`` put on the row after startup: the next tick accepts nothing."""
    svc = _watcher([dataclasses.replace(STACK, settings={"auto_accept": False, "max_open_proposals": 9})])
    await svc._iterate()
    assert svc.run_once.await_count == 1, "the stack is still enabled: it ticks"
    assert svc._auto_acceptor.await_count == 0, "auto_accept: false on the row must apply to the next tick"
    assert svc._config.watcher_max_open_proposals == 9, "every setting of the row applies"


@pytest.mark.asyncio
async def test_a_row_read_again_is_checked_against_the_watcher_section_s_bounds() -> None:
    """The row's values of the watcher section's keys are its section overrides, each checked with the section's
    bounds (#738): one past them is left out, and the deployment's value applies."""
    svc = _watcher([dataclasses.replace(STACK, settings={"auto_accept": False, "auto_accept_max_per_day": 5000})])
    await svc._iterate()
    assert svc._config.section_overrides == {"auto_accept": False}, (
        f"the row read again must be checked as at startup: {svc._config.section_overrides}"
    )


@pytest.mark.asyncio
async def test_a_changed_logql_is_asked_of_loki_before_the_stack_ticks_on_it() -> None:
    asked: list[str] = []

    def loki(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.params["query"])
        return httpx.Response(200, json={"data": {"result": []}})

    svc = _watcher([dataclasses.replace(STACK, logql='{app="api"}')])
    svc._http = _loki(loki)
    await svc._iterate()
    assert asked and asked[0] == '{app="api"}', f"the new selector must be checked before the tick reads it: {asked}"
    assert svc.run_once.await_count == 1


@pytest.mark.asyncio
async def test_a_changed_deploy_logql_alone_is_asked_of_loki_again() -> None:
    asked: list[str] = []

    def loki(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.params["query"])
        return httpx.Response(200, json={"data": {"result": []}})

    svc = _watcher([dataclasses.replace(STACK, settings={**STACK.settings, "deploy_logql": '{job="deploys"}'})])
    svc._http = _loki(loki)
    await svc._iterate()
    assert '{job="deploys"}' in asked, f"a new deploy-marker selector must be checked with Loki too: {asked}"


@pytest.mark.asyncio
async def test_a_changed_tracker_builds_the_stack_s_filer_again() -> None:
    svc = _watcher([dataclasses.replace(STACK, tracker="gitlab"), dataclasses.replace(STACK, tracker="gitlab")])
    built: list[str] = []
    svc._filer_for = lambda stack: built.append(stack.tracker) or MagicMock(tracker=stack.tracker)
    await svc._iterate()
    await svc._iterate()
    assert built == ["gitlab"], f"the filer of the stack's new tracker, built once: {built}"


@pytest.mark.asyncio
async def test_a_row_that_cannot_be_read_leaves_the_row_last_read() -> None:
    svc = _watcher([RuntimeError("database is down")])
    await svc._iterate()
    assert svc.run_once.await_count == 1, "a blip neither stops a stack"
    assert svc._auto_acceptor.await_count == 1, "nor changes what it reads"


# ---- the fleet -------------------------------------------------------------------------------------------------


class _Watcher:
    """A stack's watcher as the fleet sees it: its loop, whether a tick runs, the config its metrics read."""

    def __init__(self, stack: WatcherStack) -> None:
        self.stack = stack
        self.stack_config = SimpleNamespace(watcher_max_open_proposals=3)
        self.ticking = False
        self.looping = asyncio.Event()
        self.cancelled = False
        self.retired = False

    def retire(self) -> None:
        self.retired = True

    async def start(self) -> None:
        self.looping.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def _fleet(table: list[WatcherStack], metrics: LoopMetrics | None = None, **kw: Any) -> Any:
    unreadable = list(kw.pop("unreadable", []))

    async def read() -> tuple[list[WatcherStack], list[str]]:
        return [s for s in table if s.enabled], unreadable

    runs = MagicMock(last_tick_times=AsyncMock(return_value={}))
    return _fleet_module().WatcherFleet(
        build=_Watcher, read_stacks=read, runs_repo=runs, run_loops=True, background_tasks=set(), metrics=metrics, **kw
    )


@pytest.mark.asyncio
async def test_a_new_stack_starts_ticking() -> None:
    table: list[WatcherStack] = []
    fleet = _fleet(table)
    assert await fleet.sync() == ([], [])
    table.append(STACK)
    assert await fleet.sync() == (["acme/web"], []), "a stack added after startup must be started"
    await asyncio.wait_for(fleet.services["acme/web"].looping.wait(), 1)
    assert await fleet.sync() == ([], []), "and once"
    fleet.stop("acme/web")


@pytest.mark.asyncio
async def test_a_disabled_stack_stops_ticking_and_a_re_enabled_one_starts_again() -> None:
    table = [STACK]
    fleet = _fleet(table)
    await fleet.sync()
    watcher = fleet.services["acme/web"]
    await asyncio.wait_for(watcher.looping.wait(), 1)
    table[0] = dataclasses.replace(STACK, enabled=False)
    assert await fleet.sync() == ([], ["acme/web"]), "a disabled stack must be stopped"
    await asyncio.sleep(0)
    assert watcher.cancelled and "acme/web" not in fleet.services, "its loop is cancelled and no reader sees it"
    table[0] = STACK
    assert await fleet.sync() == (["acme/web"], [])
    fleet.stop("acme/web")


@pytest.mark.asyncio
async def test_a_stack_stopped_in_the_middle_of_a_tick_finishes_it_first() -> None:
    table = [STACK]
    fleet = _fleet(table)
    await fleet.sync()
    watcher = fleet.services["acme/web"]
    await asyncio.wait_for(watcher.looping.wait(), 1)
    watcher.ticking = True
    table.clear()
    await fleet.sync()
    await asyncio.sleep(0)
    assert not watcher.cancelled and "acme/web" not in fleet.services, "a tick under way is not cut short"
    assert await fleet.sync() == ([], []), "nor started again while it finishes"
    watcher.ticking = False
    await fleet.sync()
    await asyncio.sleep(0)
    assert watcher.cancelled, "once the tick is done, the loop stops"


@pytest.mark.asyncio
async def test_a_stack_re_enabled_while_its_watcher_finishes_a_tick_gets_no_second_watcher() -> None:
    table = [STACK]
    fleet = _fleet(table)
    await fleet.sync()
    first = fleet.services["acme/web"]
    await asyncio.wait_for(first.looping.wait(), 1)
    first.ticking = True
    table[0] = dataclasses.replace(STACK, enabled=False)
    await fleet.sync()
    table[0] = STACK
    started, _ = await fleet.sync()
    assert started == [] and "acme/web" not in fleet.services, "a second watcher would run beside the one finishing"
    first.ticking = False
    started, _ = await fleet.sync()
    await asyncio.sleep(0)
    assert first.cancelled and started == ["acme/web"] and fleet.services["acme/web"] is not first
    fleet.stop("acme/web")


@pytest.mark.asyncio
async def test_a_stack_whose_row_cannot_be_read_and_is_not_running_is_said_once() -> None:
    import structlog

    fleet = _fleet([], unreadable=["acme/deep"])
    with structlog.testing.capture_logs() as logs:
        await fleet.sync()
        await fleet.sync()
    said = [(e["log_level"], e.get("repo")) for e in logs if e["event"] == "watcher_stack_unreadable"]
    assert said == [("critical", "acme/deep")], f"named once, as at startup, while it stays unreadable: {said}"


@pytest.mark.asyncio
async def test_a_deleted_stack_s_metrics_go() -> None:
    registry = CollectorRegistry()
    metrics = LoopMetrics(registry)
    table = [STACK]
    fleet = _fleet(table, metrics)
    await fleet.sync()
    metrics.watcher_tick("no_issue", analysed=True, stack="acme/web")
    metrics.watcher_health("ok", stack="acme/web")
    metrics.watcher_open_proposals.labels(stack="acme/web").set(1)
    names = (
        "embry0_watcher_last_tick_timestamp_seconds",
        "embry0_watcher_last_verdict_tick_timestamp_seconds",
        "embry0_watcher_open_proposals",
        "embry0_watcher_open_proposals_cap",
    )
    assert all(registry.get_sample_value(name, {"stack": "acme/web"}) is not None for name in names)
    table.clear()
    await fleet.sync()
    left = [name for name in names if registry.get_sample_value(name, {"stack": "acme/web"}) is not None]
    left += [
        f"embry0_watcher_health{{state={state}}}"
        for state in WATCHER_HEALTH_STATES
        if registry.get_sample_value("embry0_watcher_health", {"state": state, "stack": "acme/web"}) is not None
    ]
    if registry.get_sample_value("embry0_watcher_ticks_total", {"action": "no_issue", "stack": "acme/web"}):
        left.append("embry0_watcher_ticks_total")
    assert left == [], f"a deleted stack's series must go, or a dead-man alert reads it as stopped: {left}"


@pytest.mark.asyncio
async def test_a_table_or_a_row_that_cannot_be_read_stops_nothing() -> None:
    table = [STACK]
    fleet = _fleet(table, unreadable=["acme/web"])
    fleet.services["acme/web"] = _Watcher(STACK)
    table.clear()
    assert await fleet.sync() == ([], []), "a running stack whose row cannot be read keeps running"

    async def broken() -> Any:
        raise RuntimeError("database is down")

    fleet._read_stacks = broken
    assert await fleet.sync() == ([], []) and "acme/web" in fleet.services, "a failed read stops nothing"


@pytest.mark.asyncio
async def test_the_registry_the_api_and_health_read_changes_in_place() -> None:
    changed: list[list[str]] = []
    table = [STACK]
    fleet = _fleet(table, on_change=lambda: changed.append(sorted(fleet.services)))
    registry = fleet.services
    await fleet.sync()
    table.clear()
    await fleet.sync()
    assert fleet.services is registry and changed == [["acme/web"], []], changed
    assert dict(fleet.views) == {}, "the metrics snapshot reads the running stacks"


@pytest.mark.asyncio
async def test_a_stack_stopped_while_the_metrics_are_read_gets_no_series_back() -> None:
    """The scrape counts each stack's drafts with awaits between them; a stack stopped meanwhile was removed with
    its series, and the scrape must not write it again."""
    from embry0.metrics.wiring import build_snapshot

    registry = CollectorRegistry()
    metrics = LoopMetrics(registry)
    stacks = {repo: SimpleNamespace(watcher_proposal_window_days=7) for repo in ("acme/api", "acme/web")}

    async def count(*, days: int, repo: str) -> int:
        if repo == "acme/web":
            stacks.pop("acme/api")  # stopped by the fleet while the scrape counts
        return 1

    snapshot = build_snapshot(
        metrics,
        healing_cases=MagicMock(),
        healing_cases_repo=MagicMock(count_open_unaccepted_watcher_cases=AsyncMock(side_effect=count)),
        jobs_repo=MagicMock(),
        stacks=stacks,
        reconciler_actor="embry0:reconciler",
    )
    await snapshot.refresh(deadline=False)
    read = {
        repo: registry.get_sample_value("embry0_watcher_open_proposals", {"stack": repo})
        for repo in stacks | {"acme/api": 0}
    }
    assert read == {"acme/web": 1.0, "acme/api": None}, f"the running stack counted, the stopped one left out: {read}"


# ---- ticks started by hand --------------------------------------------------------------------------------------


A = WatcherStack(repo="acme/api", logql='{app="api"}', tracker="github")
B = WatcherStack(repo="acme/web", logql='{app="web"}', tracker="github")
SERIES = ("embry0_watcher_last_tick_timestamp_seconds", "embry0_watcher_last_verdict_tick_timestamp_seconds")


def _real_fleet(table: dict[str, WatcherStack], metrics: LoopMetrics, on_read: Any = None) -> Any:
    """Real watchers of ``table``'s stacks in a real fleet; ``on_read(repo)`` runs as a watcher reads its row."""

    def build(stack: WatcherStack) -> Any:
        svc = _service(config=StackConfig(_config(), stack), lines=[])
        svc._metrics = metrics
        svc._query_checks = {"WATCHER_LOGQL": LogQLCheck(LogQLStatus.OK)}
        svc._auto_acceptor = AsyncMock(return_value=[])

        async def read_row(repo: str = stack.repo) -> WatcherStack:
            if on_read is not None:
                await on_read(repo)
            return table[repo]

        svc._stack_reader = read_row
        return svc

    async def read_stacks() -> tuple[list[WatcherStack], list[str]]:
        return [s for s in table.values() if s.enabled], []

    return _fleet_module().WatcherFleet(
        build=build,
        read_stacks=read_stacks,
        runs_repo=MagicMock(last_tick_times=AsyncMock(return_value={})),
        run_loops=False,
        background_tasks=set(),
        metrics=metrics,
    )


def _request(fleet: Any) -> Any:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(watcher_services=fleet.services, watcher_service=None))
    )


@pytest.mark.asyncio
async def test_an_unnamed_tick_runs_no_stack_the_fleet_stopped_while_it_checked_the_others(monkeypatch) -> None:
    """#749 review: the dashboard's Run tick now checks each stack's row, then ticks them; a stack the fleet stopped
    in between must not pay for an analysis, nor get the series back that its stop removed."""
    from embry0.api.v1 import watcher as watcher_api

    registry = CollectorRegistry()
    metrics = LoopMetrics(registry)
    table = {"acme/api": A, "acme/web": B}
    armed = {"on": False}

    async def on_read(repo: str) -> None:
        if repo == "acme/web" and armed["on"]:
            armed["on"] = False  # acme/api is disabled, and the fleet's minute lands, while acme/web's row is read
            table["acme/api"] = dataclasses.replace(A, enabled=False)
            await fleet.sync()

    fleet = _real_fleet(table, metrics, on_read)
    await fleet.sync()
    api = fleet.services["acme/api"]
    monkeypatch.setattr(watcher_api, "key_covers", lambda request, name: True)
    armed["on"] = True
    await watcher_api.trigger_watcher_run(_request(fleet))
    back = [name for name in SERIES if registry.get_sample_value(name, {"stack": "acme/api"}) is not None]
    assert api._runs.create.await_count == 0, "a stack stopped after its check still ticked"
    assert back == [], f"and got its series back, which nothing removes again: {back}"


@pytest.mark.asyncio
async def test_a_named_tick_of_a_stack_stopped_after_its_check_answers_as_not_watched(monkeypatch) -> None:
    """The named path awaits the run_with check between the row check and the tick: the fleet can stop the stack
    there too."""
    from fastapi import HTTPException

    from embry0.api.v1 import watcher as watcher_api

    registry = CollectorRegistry()
    metrics = LoopMetrics(registry)
    fleet = _real_fleet({"acme/api": A}, metrics)
    await fleet.sync()
    api = fleet.services["acme/api"]

    async def stopped_meanwhile(request: Any, chosen: Any, stack: str) -> None:
        fleet.stop(stack)

    monkeypatch.setattr(watcher_api, "key_covers", lambda request, name: True)
    monkeypatch.setattr(watcher_api, "checked_run_with", stopped_meanwhile)
    try:
        answered = await watcher_api.trigger_watcher_run(_request(fleet), stack="acme/api")
    except HTTPException as refused:
        assert refused.status_code == 404, refused.detail
    else:
        pytest.fail(f"a stack the fleet stopped after its check was ticked by hand: {answered}")
    assert api._runs.create.await_count == 0, "a stack stopped after its check still ticked"
    assert all(registry.get_sample_value(name, {"stack": "acme/api"}) is None for name in SERIES)


@pytest.mark.asyncio
async def test_an_unnamed_tick_reads_every_stack_s_row_at_once(monkeypatch) -> None:
    """Each check waits for its stack's tick under way; one after another, a busy stack would hold up the rest."""
    from embry0.api.v1 import watcher as watcher_api

    both = asyncio.Event()
    reading: set[str] = set()
    overlapped: list[str] = []

    async def on_read(repo: str) -> None:
        reading.add(repo)
        if len(reading) == 2:
            both.set()
        try:
            await asyncio.wait_for(both.wait(), 0.5)
        except TimeoutError:
            return
        overlapped.append(repo)

    fleet = _real_fleet({"acme/api": A, "acme/web": B}, LoopMetrics(CollectorRegistry()), on_read)
    await fleet.sync()
    monkeypatch.setattr(watcher_api, "key_covers", lambda request, name: True)
    result = await watcher_api.trigger_watcher_run(_request(fleet))
    assert sorted(overlapped) == ["acme/api", "acme/web"], (
        f"the unnamed tick read one stack's row after another, not every row at once: {overlapped}"
    )
    assert [entry["stack"] for entry in result["stacks"]] == ["acme/api", "acme/web"], result


# ---- production ------------------------------------------------------------------------------------------------


def _watcher_service_call() -> ast.Call:
    for node in ast.walk(ast.parse(pathlib.Path(app_module.__file__).read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "WatcherService":
            return node
    pytest.fail("no WatcherService(...) construction found in embry0/api/app.py")


def test_each_watcher_reads_its_own_stack_s_row() -> None:
    passed = {kw.arg: ast.unparse(kw.value) for kw in _watcher_service_call().keywords if kw.arg}
    assert passed.get("stack_reader") == "_watcher_stack(app, stack.repo)", (
        f"each watcher must read its own stack's row before a tick, not {passed.get('stack_reader')!r}"
    )


@pytest.mark.asyncio
async def test_the_row_reader_reads_the_stack_now() -> None:
    reader_for = getattr(app_module, "_watcher_stack", None)
    assert reader_for is not None, "app.py has no reader of a stack's row"
    repo = MagicMock(get=AsyncMock(side_effect=[STACK, None]))
    read = reader_for(SimpleNamespace(state=SimpleNamespace(watcher_stacks_repo=repo)), "acme/web")
    assert await read() == STACK and await read() is None, "read on every call"
    assert [c.args for c in repo.get.await_args_list] == [("acme/web",), ("acme/web",)]
