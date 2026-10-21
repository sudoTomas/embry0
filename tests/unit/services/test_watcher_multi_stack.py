"""One watcher per stack: the tick route, the metrics and the wiring (#378)."""

from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from prometheus_client import CollectorRegistry

import embry0.api.app as app_module
import embry0.api.v1.watcher as watcher_api
from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.metrics.loop import LoopMetrics, sample
from embry0.services.draft_filers import GitHubDraftFiler, LinearDraftFiler, build_filer
from embry0.services.watcher_stacks import WatcherStack

STACK = "acme/backend"
SELF = "example-org/embry0"


# -- POST /watcher/run ----------------------------------------------------------


def _app(services: dict[str, Any]) -> Any:
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config)
    app.state.watcher_services = services
    app.state.watcher_service = next(iter(services.values()), None)
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    )


def _service(action: str = "no_issue") -> Any:
    return SimpleNamespace(run_once=AsyncMock(return_value={"action": action}))


async def test_one_stack_still_ticks_without_being_named() -> None:
    services = {STACK: _service()}
    async with _client(_app(services)) as client:
        resp = await client.post("/api/v1/watcher/run")
    assert resp.status_code == 200
    services[STACK].run_once.assert_awaited_once()


async def test_with_two_stacks_an_unnamed_tick_runs_every_stack() -> None:
    """The dashboard's Run tick now and MCP watcher_trigger name no stack.
    With two stacks that answered 400 and ticked nothing. Unnamed, every
    stack ticks, and the answer says what each one did."""
    services = {STACK: _service("no_issue"), SELF: _service("proposed")}
    async with _client(_app(services)) as client:
        resp = await client.post("/api/v1/watcher/run")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action"] == "every_stack_ticked"
    assert body["stacks"] == [{"stack": STACK, "action": "no_issue"}, {"stack": SELF, "action": "proposed"}]
    for service in services.values():
        service.run_once.assert_awaited_once()


async def test_an_unnamed_tick_needs_a_key_that_may_tick_every_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    """A restricted key is checked against each stack before any ticks, so
    it never starts some analyses and is refused the rest."""

    async def _only_backend(request: Any, repo: str | None, *, action: str) -> None:
        if repo != STACK:
            raise HTTPException(status_code=403, detail=f"repo_not_allowed: {repo}")

    monkeypatch.setattr(watcher_api, "enforce_repo_allowlist", _only_backend)
    services = {STACK: _service(), SELF: _service()}
    async with _client(_app(services)) as client:
        resp = await client.post("/api/v1/watcher/run")
    assert resp.status_code == 403, resp.text
    for service in services.values():
        service.run_once.assert_not_awaited()


async def test_a_named_stack_ticks_only_itself() -> None:
    services = {STACK: _service(), SELF: _service("proposed")}
    async with _client(_app(services)) as client:
        resp = await client.post(f"/api/v1/watcher/run?stack={SELF}")
    assert resp.json()["action"] == "proposed"
    services[SELF].run_once.assert_awaited_once()
    services[STACK].run_once.assert_not_awaited()


async def test_an_unwatched_stack_is_404() -> None:
    async with _client(_app({STACK: _service()})) as client:
        resp = await client.post("/api/v1/watcher/run?stack=nobody/nothing")
    assert resp.status_code == 404


# -- metrics --------------------------------------------------------------------


def test_ticks_health_and_seeding_are_per_stack() -> None:
    """Sharing one series, a stuck stack hides behind a busy one — the
    dead-man alerts would read "something ticked" as "everything ticks"."""
    metrics = LoopMetrics(registry=(registry := CollectorRegistry()))

    metrics.watcher_tick("no_issue", analysed=True, at=1_000.0, stack=STACK)
    metrics.watcher_tick("error", analysed=False, at=2_000.0, stack=SELF)
    metrics.watcher_health("ok", stack=STACK)
    metrics.watcher_health("invalid_logql", stack=SELF)

    assert sample(registry, "embry0_watcher_last_tick_timestamp_seconds", {"stack": STACK}) == 1_000.0
    assert sample(registry, "embry0_watcher_last_tick_timestamp_seconds", {"stack": SELF}) == 2_000.0
    # An errored tick analysed nothing, so that stack has no analysed series
    # at all — which is what the dead-man rule's `> 0` guard expects.
    assert sample(registry, "embry0_watcher_last_analysed_tick_timestamp_seconds", {"stack": SELF}) is None
    assert sample(registry, "embry0_watcher_health", {"state": "ok", "stack": STACK}) == 1.0
    assert sample(registry, "embry0_watcher_health", {"state": "ok", "stack": SELF}) == 0.0
    assert sample(registry, "embry0_watcher_health", {"state": "invalid_logql", "stack": SELF}) == 1.0
    assert sample(registry, "embry0_watcher_ticks_total", {"action": "error", "stack": SELF}) == 1.0
    assert sample(registry, "embry0_watcher_ticks_total", {"action": "error", "stack": STACK}) is None


def test_seeding_never_moves_a_stacks_timestamp_backwards() -> None:
    from datetime import UTC, datetime

    metrics = LoopMetrics(registry=(registry := CollectorRegistry()))
    metrics.watcher_tick("no_issue", analysed=True, at=5_000.0, stack=STACK)
    metrics.seed_watcher_ticks(
        last_tick_at=datetime.fromtimestamp(1_000, UTC), last_analysed_at=None, last_verdict_at=None, stack=STACK
    )
    assert sample(registry, "embry0_watcher_last_tick_timestamp_seconds", {"stack": STACK}) == 5_000.0


# -- filers per stack -------------------------------------------------------------


def test_the_filer_follows_the_stack_tracker() -> None:
    linear = object()
    acme = WatcherStack(repo=STACK, logql="{x=1}", tracker="linear", tracker_target={"team_id": "team-1"})
    own = WatcherStack(repo=SELF, logql="{x=1}", tracker="github")

    assert isinstance(build_filer(acme, linear_sync=linear), LinearDraftFiler)
    github = build_filer(own, github_token="t")
    assert isinstance(github, GitHubDraftFiler)
    assert github.target == SELF, "a github stack files into its own repo by default"

    elsewhere = WatcherStack(repo=SELF, logql="{x=1}", tracker="github", tracker_target={"repo": "owner/tracker"})
    assert build_filer(elsewhere, github_token="t").target == "owner/tracker"


def test_a_stack_whose_tracker_cannot_be_built_gets_no_filer() -> None:
    """None is not an error: the tick records it and files nothing, rather
    than filing this stack's findings into another stack's tracker."""
    unconfigured = WatcherStack(repo=STACK, logql="{x=1}", tracker="linear", tracker_target={})
    assert build_filer(unconfigured, linear_sync=object()) is None
    assert build_filer(WatcherStack(repo=STACK, logql="{x=1}", tracker="linear"), linear_sync=None) is None


# -- production wiring ------------------------------------------------------------


def _tree() -> ast.Module:
    return ast.parse(pathlib.Path(app_module.__file__).read_text())


def test_a_watcher_is_built_and_started_for_every_enabled_stack() -> None:
    source = pathlib.Path(app_module.__file__).read_text()
    assert "list_all(enabled_only=True)" in source, "app.py must read the enabled stacks"
    assert "app.state.watcher_services = {" in source

    started = [
        n
        for n in ast.walk(_tree())
        if isinstance(n, ast.Call)
        and ast.unparse(n.func) == "asyncio.create_task"
        and ast.unparse(n.args[0]) == "service.start()"
    ]
    assert started, "app.py starts no per-stack watcher loop"


def test_each_watcher_gets_its_own_stack_config_and_filer() -> None:
    """One shared config would give every stack the env stack's repo and
    selector — two watchers reading the same logs and writing the same rows."""
    call = next(
        n
        for n in ast.walk(_tree())
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "WatcherService"
    )
    passed = {kw.arg: ast.unparse(kw.value) for kw in call.keywords if kw.arg}
    assert passed["config"] == "StackConfig(config, stack)"
    assert passed["filer"].startswith("build_filer(")


@pytest.mark.parametrize("attr", ["watcher_services", "watcher_service"])
def test_both_state_slots_are_set(attr: str) -> None:
    """`watcher_service` stays for the single-stack callers."""
    assert f"app.state.{attr} = " in pathlib.Path(app_module.__file__).read_text()


async def test_startup_seeding_lands_on_the_stacks_own_series() -> None:
    """Seeded onto the unlabelled series instead, every restart would read as
    "never ticked" to the dead-man alerts, which query the stack's own."""
    from datetime import UTC, datetime

    from embry0.metrics.wiring import seed_watcher_ticks

    when = datetime(2026, 9, 20, 6, 30, tzinfo=UTC)
    metrics = LoopMetrics(registry=(registry := CollectorRegistry()))
    verdict = datetime(2026, 9, 20, 7, 30, tzinfo=UTC)  # a quiet tick after the last analysis
    runs = SimpleNamespace(
        last_tick_times=AsyncMock(
            return_value={"last_tick_at": verdict, "last_analysed_at": when, "last_verdict_at": verdict}
        )
    )

    await seed_watcher_ticks(metrics, runs, repo=STACK)

    runs.last_tick_times.assert_awaited_once_with(repo=STACK)
    assert sample(registry, "embry0_watcher_last_tick_timestamp_seconds", {"stack": STACK}) == verdict.timestamp()
    assert sample(registry, "embry0_watcher_last_analysed_tick_timestamp_seconds", {"stack": STACK}) == when.timestamp()
    # The wiring must hand the verdict time through — the dead-man reads that gauge.
    assert (
        sample(registry, "embry0_watcher_last_verdict_tick_timestamp_seconds", {"stack": STACK}) == verdict.timestamp()
    )
    assert sample(registry, "embry0_watcher_last_tick_timestamp_seconds", {"stack": ""}) is None
