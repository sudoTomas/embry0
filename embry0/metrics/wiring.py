"""Attach the loop metrics to the FastAPI app — the only place that knows both.

``/metrics`` was already served by ``prometheus-fastapi-instrumentator``; this
keeps that endpoint and that library and adds to them:

* one ``CollectorRegistry`` per app (not the process-global one, which cannot
  be created twice — every test builds an app) holding the HTTP metrics, the
  process/platform/GC collectors the global registry used to provide,
  :class:`LoopMetrics` and the off-host backup's freshness (#581);
* a route dependency that refreshes the DB-backed gauges before the
  instrumentator renders the registry (see ``snapshot.py`` for why that can
  neither fail nor stall the scrape);
* an exclusion for the trailing-slash redirects of parametrised routes, which
  the instrumentator would otherwise label by raw path (#507).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import Depends, FastAPI, Request
from prometheus_client import CollectorRegistry, GCCollector, PlatformCollector, ProcessCollector

from embry0.api.route_table import served_routes
from embry0.metrics.labels import resolve_error_categoriser
from embry0.metrics.loop import LoopMetrics
from embry0.metrics.offhost_backup import OffhostBackupCollector
from embry0.metrics.snapshot import LoopSnapshot, cases_source, gauge_source, queue_source, stack_gauge_source

if TYPE_CHECKING:
    from embry0.config import Embry0Config

logger = structlog.get_logger(__name__)


async def _refresh_snapshot(request: Request) -> None:
    snapshot: LoopSnapshot | None = getattr(request.app.state, "loop_snapshot", None)
    if snapshot is not None:
        await snapshot.refresh()


def install_metrics(app: FastAPI, config: Embry0Config) -> LoopMetrics | None:
    """Create the app's registry and ``LoopMetrics``, and expose ``/metrics``.

    LAN-only by network topology: nginx proxies no ``/metrics`` location on
    either vhost and the webhooks tunnel admits two webhook paths only, so the
    endpoint is reachable on the compose networks (where Prometheus scrapes
    ``embry0-orchestrator:8000``) and nowhere else. A test pins the nginx half.

    Never raises. Metrics are an optional subsystem, so they must not be able
    to stop the orchestrator from starting (the rule a duplicate metric name
    introduced by a rebase would otherwise break, since ``create_app`` runs
    before uvicorn binds). On failure the API comes up without ``/metrics``,
    ``app.state.loop_metrics`` is None, and every producer falls back to the
    null facade.
    """
    try:
        return _install_metrics(app, config)
    except Exception:
        app.state.loop_metrics = None
        logger.critical(
            "loop_metrics_install_failed",
            exc_info=True,
            hint="the API is up but serves no /metrics; every loop alert will read as a scrape failure",
        )
        return None


def _install_metrics(app: FastAPI, config: Embry0Config) -> LoopMetrics:
    from prometheus_fastapi_instrumentator import Instrumentator

    registry = CollectorRegistry()
    ProcessCollector(registry=registry)
    PlatformCollector(registry=registry)
    GCCollector(registry=registry)
    metrics = LoopMetrics(registry, categorise_error=resolve_error_categoriser())
    registry.register(
        OffhostBackupCollector(enabled=config.offhost_backup_enabled, status_dir=Path(config.offhost_backup_status_dir))
    )
    app.state.loop_metrics = metrics
    Instrumentator(registry=registry, excluded_handlers=_slash_redirect_patterns(app)).instrument(app).expose(
        app, endpoint="/metrics", dependencies=[Depends(_refresh_snapshot)]
    )
    return metrics


def _slash_redirect_patterns(app: FastAPI) -> list[str]:
    """Handler patterns for the trailing-slash redirects of parametrised routes (#507).

    Starlette answers ``/api/v1/jobs/<id>/`` with a redirect to the route
    without the slash. The instrumentator (8.x) labels that request by its raw
    path, ID included, so every ID would mint its own ``handler`` series. Each
    pattern is a route's own regex with the slash appended, so it matches the
    paths Starlette redirects to that route and nothing else: never a template
    (a test pins that none matches), and never an unmatched path, which still
    counts as ``none``. A route whose path ends in ``/`` also serves its path
    without the slash, so nothing redirects the other way (a test pins that too). Static routes are left out: their
    slash path is one fixed value, the same label 7.x gave it.

    Reads the routes registered so far, so it must run after them. Walks the
    served routes, not ``app.routes``: since FastAPI 0.137 that holds one
    opaque entry per included router, and its regexes would lack the prefix.
    """
    return [
        route.path_regex.pattern.removesuffix("$") + "/$" for route in served_routes(app.routes) if "{" in route.path
    ]


def build_snapshot(
    metrics: LoopMetrics,
    *,
    healing_cases: Any,
    healing_cases_repo: Any,
    jobs_repo: Any,
    stacks: Mapping[str, Any],
    reconciler_actor: str,
) -> LoopSnapshot:
    """The production sources. Order is scrape priority: if the shared
    deadline runs out, the later ones are the ones left stale.

    ``stacks`` maps each watched stack to its own config view. The cap count
    is asked per stack with that stack's window, which is the question the
    watcher's tick asks before it files (#448) — not one count for all.
    """

    async def open_proposals() -> dict[str, int]:
        return {
            stack: int(
                await healing_cases_repo.count_open_unaccepted_watcher_cases(
                    days=view.watcher_proposal_window_days, repo=stack
                )
            )
            for stack, view in stacks.items()
        }

    async def reconciler_daily() -> int:
        since = datetime.now(UTC) - timedelta(days=1)
        return int(await jobs_repo.count_dispatched_by(reconciler_actor, since=since))

    return LoopSnapshot(
        metrics,
        {
            "queue": queue_source(metrics, jobs_repo.queue_summary),
            "cases": cases_source(metrics, healing_cases.metrics_snapshot),
            "watcher_open_proposals": stack_gauge_source(metrics.watcher_open_proposals, open_proposals),
            "reconciler_daily_dispatches": gauge_source(metrics.reconciler_daily_dispatches, reconciler_daily),
        },
    )


async def seed_watcher_ticks(metrics: LoopMetrics, watcher_runs_repo: Any, repo: str | None = None) -> None:
    """Startup: restore the tick timestamps from ``watcher_runs``. Never raises
    — an unseeded gauge is a false alarm at worst, not a reason to stay down."""
    try:
        times = await watcher_runs_repo.last_tick_times(repo=repo)
    except Exception:
        logger.warning("loop_metrics_watcher_seed_failed", exc_info=True)
        return
    # #378: the series is per stack. Seeding without it would restore the
    # timestamps onto the unlabelled series while the dead-man alerts read the
    # stack's own — every restart looking like "never ticked".
    metrics.seed_watcher_ticks(
        last_tick_at=times.get("last_tick_at"),
        last_analysed_at=times.get("last_analysed_at"),
        last_verdict_at=times.get("last_verdict_at"),
        stack=repo or "",
    )
