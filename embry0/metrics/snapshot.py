"""The DB-backed gauges: refreshed on scrape, cached, and unable to hurt the API.

Open cases, queue depth and cap occupancy are facts in Postgres, so a counter
kept in process memory would be wrong after every restart. They are read when
Prometheus scrapes ``/metrics`` instead, under four rules:

* **cached** — at most one refresh per ``ttl_seconds``, however many scrapers;
* **single-flight, never queued** — a scrape that arrives during a refresh is
  served the previous values at once;
* **bounded** — the sources run one after another (one pool connection at
  most) against one shared deadline of ``timeout_seconds``; a source that
  overruns is cancelled, which cancels its query;
* **fail-soft** — a failed or timed-out source counts in
  ``embry0_loop_snapshot_errors_total{source}``, leaves its gauges at their
  last value, and the scrape still answers 200. The TTL also applies after a
  failure, so a sick database is not retried on every scrape.

``embry0_loop_snapshot_last_success_timestamp_seconds`` moves only when every
source succeeded — alert on its age to catch gauges that have gone stale.

A source is ``fetch`` (async, touches the DB, returns plain data) plus
``apply`` (sync, writes gauges). Keeping them apart means the timeout can
never interrupt a half-written set of gauges.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import structlog

from embry0.metrics.labels import enum_label
from embry0.metrics.loop import QUEUE_STATUSES, LoopMetrics

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SnapshotSource:
    fetch: Callable[[], Awaitable[Any]]
    apply: Callable[[Any], None]


class LoopSnapshot:
    def __init__(
        self,
        metrics: LoopMetrics,
        sources: Mapping[str, SnapshotSource],
        *,
        ttl_seconds: float = 30.0,
        timeout_seconds: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._metrics = metrics
        self._sources = dict(sources)
        self._ttl = ttl_seconds
        self._timeout = timeout_seconds
        self._clock = clock
        self._refreshed_at: float | None = None
        self._refreshing = False

    async def refresh(self, *, deadline: bool = True) -> None:
        """Bring the gauges up to date if they are stale. Never raises.

        ``deadline=False`` reads every source to its end, however long it takes, instead of giving the sources
        ``timeout_seconds`` between them: the gauges as the sources stand, for a caller that must have every
        one (a test of what the sources read). A scrape never waits so.
        """
        now = self._clock()
        if self._refreshing or (self._refreshed_at is not None and now - self._refreshed_at < self._ttl):
            return
        self._refreshing = True
        try:
            until = now + self._timeout
            failed = False
            for name, source in self._sources.items():
                try:
                    if deadline:
                        remaining = until - self._clock()
                        if remaining <= 0:
                            raise TimeoutError
                        data = await asyncio.wait_for(source.fetch(), timeout=remaining)
                    else:
                        data = await source.fetch()
                    source.apply(data)
                except Exception as exc:
                    failed = True
                    self._metrics.snapshot_errors.labels(source=name).inc()
                    logger.warning("loop_snapshot_source_failed", source=name, error=type(exc).__name__)
            if not failed:
                self._metrics.snapshot_last_success.set(time.time())
        finally:
            self._refreshed_at = self._clock()
            self._refreshing = False


# ---- the production sources ----------------------------------------------------


def cases_source(metrics: LoopMetrics, fetch: Callable[[], Awaitable[dict[str, Any]]]) -> SnapshotSource:
    """``fetch`` is ``HealingCaseService.metrics_snapshot``."""
    from embry0.services.healing.lifecycle import Origin, ResolutionPath, Stage

    open_stages = [s.value for s in Stage if s is not Stage.CLOSED]
    seen_open: set[tuple[str, str]] = {(stage, origin.value) for stage in open_stages for origin in Origin}
    seen_closed: set[str] = {p.value for p in ResolutionPath}

    def apply(data: dict[str, Any]) -> None:
        # Every (stage, origin) is written on every refresh: a combination
        # that emptied must read 0, not keep its last count forever.
        counts: dict[tuple[str, str], int] = {}
        for row in data["open"]:
            key = (enum_label(Stage, row["stage"]), enum_label(Origin, row["origin"]))
            counts[key] = counts.get(key, 0) + int(row["count"])
        seen_open.update(counts)
        for stage, origin in seen_open:
            metrics.cases_open.labels(stage=stage, origin=origin).set(counts.get((stage, origin), 0))
        oldest = {enum_label(Stage, stage): float(age) for stage, age in data["oldest_age_seconds"].items()}
        for stage in open_stages:
            metrics.cases_oldest_age.labels(stage=stage).set(max(oldest.get(stage, 0.0), 0.0))
        metrics.cases_awaiting_verdict.set(int(data["awaiting_verdict"]))
        closed: dict[str, int] = {}
        for path, count in data["closed"].items():
            label = enum_label(ResolutionPath, path)
            closed[label] = closed.get(label, 0) + int(count)
        seen_closed.update(closed)
        for path in seen_closed:
            metrics.cases_closed.labels(resolution_path=path).set(closed.get(path, 0))

    return SnapshotSource(fetch, apply)


def queue_source(metrics: LoopMetrics, fetch: Callable[[], Awaitable[dict[str, int]]]) -> SnapshotSource:
    """``fetch`` is ``JobsRepository.queue_summary``."""

    def apply(data: dict[str, int]) -> None:
        for status in QUEUE_STATUSES:
            metrics.jobs_in_flight.labels(status=status).set(int(data.get(status, 0)))

    return SnapshotSource(fetch, apply)


def gauge_source(gauge: Any, fetch: Callable[[], Awaitable[int]]) -> SnapshotSource:
    """One number into one unlabelled gauge (cap occupancies)."""
    return SnapshotSource(fetch, lambda value: gauge.set(int(value)))


def stack_gauge_source(gauge: Any, fetch: Callable[[], Awaitable[Mapping[str, int]]]) -> SnapshotSource:
    """One number per watched stack into a gauge labelled ``stack``."""

    def apply(values: Mapping[str, int]) -> None:
        for stack, value in values.items():
            gauge.labels(stack=stack).set(int(value))

    return SnapshotSource(fetch, apply)
