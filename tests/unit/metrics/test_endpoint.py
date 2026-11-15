"""/metrics — one endpoint, one registry per app, immune to the database (#285)."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from prometheus_client import ProcessCollector
from structlog.testing import capture_logs

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.metrics import LoopMetrics
from embry0.metrics.snapshot import LoopSnapshot, gauge_source
from embry0.metrics.wiring import build_snapshot, seed_watcher_ticks

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def _no_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield


def _app() -> FastAPI:
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True, api_key="")
    return create_app(config, lifespan_override=_no_lifespan)


async def _scrape(app: FastAPI) -> tuple[int, str]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/metrics")
    return response.status_code, response.text


async def test_metrics_serves_loop_http_and_process_metrics_from_one_endpoint() -> None:
    app = _app()
    app.state.loop_metrics.watcher_tick("no_issue", analysed=True)
    await _scrape(app)  # the first scrape is itself the first observed request
    status, body = await _scrape(app)
    assert status == 200
    assert 'embry0_watcher_ticks_total{action="no_issue",stack=""} 1.0' in body
    assert "http_requests_total" in body  # the instrumentator's metrics kept their names
    assert "python_info" in body
    # The process collector moved with them. It is registered on every platform,
    # but it emits nothing without Linux /proc (it reads /proc/stat for the boot
    # time), so the series itself is asserted only where /proc exists (#436).
    registry = app.state.loop_metrics.registry
    collectors = list(registry._collector_to_names)  # noqa: SLF001 — no public listing
    assert any(isinstance(c, ProcessCollector) for c in collectors), (
        f"no ProcessCollector on the app's registry: {[type(c).__name__ for c in collectors]}"
    )
    if Path("/proc/stat").exists():
        assert "process_cpu_seconds_total" in body, "the process collector is registered but its series is missing"


async def test_each_app_has_its_own_registry() -> None:
    first, second = _app(), _app()  # a process-global registry would refuse the second
    first.state.loop_metrics.watcher_tick("error", analysed=False)
    _, body = await _scrape(second)
    assert 'embry0_watcher_ticks_total{action="error",stack=""}' not in body


async def test_a_dead_database_does_not_fail_the_scrape() -> None:
    app = _app()
    metrics: LoopMetrics = app.state.loop_metrics

    async def dead() -> int:
        raise ConnectionRefusedError("postgres is down")

    app.state.loop_snapshot = LoopSnapshot(metrics, {"cap": gauge_source(metrics.reconciler_daily_dispatches, dead)})
    status, body = await _scrape(app)
    assert status == 200
    assert 'embry0_loop_snapshot_errors_total{source="cap"} 1.0' in body


async def test_a_hung_database_does_not_stall_the_scrape() -> None:
    app = _app()
    metrics: LoopMetrics = app.state.loop_metrics

    async def hung() -> int:
        await asyncio.sleep(60)
        return 1

    app.state.loop_snapshot = LoopSnapshot(
        metrics, {"cap": gauge_source(metrics.reconciler_daily_dispatches, hung)}, timeout_seconds=0.1
    )
    started = time.monotonic()
    status, _ = await _scrape(app)
    assert status == 200
    assert time.monotonic() - started < 2.0


class _Repo:
    """Just enough of each repository for build_snapshot / seed_watcher_ticks."""

    def __init__(self, **answers: Any) -> None:
        self._answers = answers
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        async def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, kwargs))
            answer = self._answers[name]
            if isinstance(answer, Exception):
                raise answer
            return answer

        return call


async def test_build_snapshot_reads_the_cap_the_watcher_is_bound_by() -> None:
    app = _app()
    metrics: LoopMetrics = app.state.loop_metrics
    cases_repo = _Repo(count_open_unaccepted_watcher_cases=7)
    jobs_repo = _Repo(
        queue_summary={"pending": 1, "running": 2, "awaiting_input": 0, "paused": 0}, count_dispatched_by=3
    )
    cases = _Repo(metrics_snapshot={"open": [], "oldest_age_seconds": {}, "awaiting_verdict": 0, "closed": {}})
    app.state.loop_snapshot = build_snapshot(
        metrics,
        healing_cases=cases,
        healing_cases_repo=cases_repo,
        jobs_repo=jobs_repo,
        # Each stack is asked about with its own window (#448).
        stacks={
            "acme/backend": SimpleNamespace(watcher_proposal_window_days=7),
            "acme/widgets": SimpleNamespace(watcher_proposal_window_days=3),
        },
        reconciler_actor="linear-reconciler",
    )
    # deadline=False: what the sources read, not whether a loaded host ran them within the scrape's 2 s; the
    # deadline is test_snapshot.py's (#421). The scrape then renders this refresh, still within the ttl.
    await app.state.loop_snapshot.refresh(deadline=False)
    _, body = await _scrape(app)
    assert 'embry0_watcher_open_proposals{stack="acme/backend"} 7.0' in body
    assert 'embry0_watcher_open_proposals{stack="acme/widgets"} 7.0' in body
    assert "embry0_linear_reconciler_daily_dispatches 3.0" in body
    assert 'embry0_jobs_in_flight{status="running"} 2.0' in body
    assert re.search(r"embry0_loop_snapshot_last_success_timestamp_seconds 1\.\d+e\+09", body)
    assert cases_repo.calls == [
        ("count_open_unaccepted_watcher_cases", {"days": 7, "repo": "acme/backend"}),
        ("count_open_unaccepted_watcher_cases", {"days": 3, "repo": "acme/widgets"}),
    ]
    name, kwargs = jobs_repo.calls[-1]
    assert name == "count_dispatched_by" and kwargs["since"] < datetime.now(UTC)


async def test_seeding_survives_a_database_error() -> None:
    metrics = LoopMetrics()
    await seed_watcher_ticks(metrics, _Repo(last_tick_times=ConnectionError("down")))
    when = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
    await seed_watcher_ticks(metrics, _Repo(last_tick_times={"last_tick_at": when, "last_analysed_at": when}))
    assert (
        metrics.registry.get_sample_value("embry0_watcher_last_analysed_tick_timestamp_seconds", {"stack": ""})
        == when.timestamp()
    )


async def test_a_broken_metrics_install_does_not_stop_the_app_from_starting(monkeypatch: pytest.MonkeyPatch) -> None:
    """An optional subsystem must never take the orchestrator down with it.
    create_app runs before uvicorn binds, so a duplicate metric name from a
    rebase would otherwise mean the API never comes up at all."""

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise ValueError("Duplicated timeseries in CollectorRegistry")

    monkeypatch.setattr("embry0.metrics.wiring.LoopMetrics", boom)
    with capture_logs() as logs:
        app = _app()

    assert app.state.loop_metrics is None
    critical = [entry for entry in logs if entry["event"] == "loop_metrics_install_failed"]
    assert critical and critical[0]["log_level"] == "critical"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/metrics")).status_code == 404

    # And every producer built against it still works, recording nothing.
    from embry0.metrics import NULL_METRICS
    from embry0.services.watcher import WatcherService

    watcher = WatcherService(
        config=app.state.config,
        jobs_repo=None,
        watcher_runs_repo=None,
        integration_repo=None,
        executor=None,
        filer=None,
        metrics=app.state.loop_metrics,
    )
    assert watcher._metrics is NULL_METRICS


async def test_every_metric_is_documented_and_every_documented_metric_exists() -> None:
    """docs/ops/metrics.md is the contract #286 and #287 write expressions
    against, so a metric that is not in it — or a name the doc still carries
    after a rename — is a broken contract, not a docs nit."""
    doc = (Path(__file__).parents[3] / "docs" / "ops" / "metrics.md").read_text()
    # The registry create_app builds, so a collector registered beside
    # LoopMetrics (the off-host backup's, #581) is held to the same contract.
    registry = _app().state.loop_metrics.registry
    exposed = {family.name for family in registry.collect() if family.name.startswith("embry0_")}
    # The first cell of every table row, so prose mentions (a network name, a
    # label value) are not read as metric names.
    documented = {
        # Counters are documented under their exposed `_total` name.
        name.removesuffix("_total")
        for row in re.findall(r"^\|([^|]*)\|", doc, re.M)
        for name in re.findall(r"`(embry0_[a-z0-9_]+)`", row)
    }

    assert exposed, "LoopMetrics registered nothing?"
    assert not exposed - documented, f"undocumented metrics: {sorted(exposed - documented)}"
    assert not documented - exposed, f"documented but not exposed: {sorted(documented - exposed)}"


@pytest.mark.parametrize("conf_path", ["nginx.conf", "nginx/dashboard-public.conf.template"])
async def test_nginx_proxies_no_metrics_location(conf_path: str) -> None:
    """The endpoint is LAN-only because nothing in front of the orchestrator
    forwards it: /metrics falls through to the SPA on both vhosts (the LAN one
    and the public one rendered from the template). Prometheus scrapes
    embry0-orchestrator:8000 on the compose network instead."""
    conf = (Path(__file__).parents[3] / "frontend" / conf_path).read_text()
    locations = re.findall(r"location\s+(\S+)\s*\{", conf)
    assert locations, f"{conf_path} has no location blocks?"
    assert not [loc for loc in locations if "metrics" in loc]
    # `location /` must stay a static SPA fallback, never a catch-all proxy.
    for block in re.findall(r"location\s+/\s*\{([^}]*)\}", conf):
        assert "proxy_pass" not in block
