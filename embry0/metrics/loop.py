"""Prometheus metrics for the self-healing loop (#285, SH-P2.1).

One facade, :class:`LoopMetrics`, owns every metric name. The rest of the
codebase never imports ``prometheus_client``: a module that produces an event
is handed a ``LoopMetrics`` and calls the one method named after that event.
A module built without one gets a private instance on a registry nobody
scrapes, so it stays independently constructible and testable; tests that
assert on values pass a ``LoopMetrics(CollectorRegistry())`` of their own.

Two kinds of metric live here:

* **event metrics** (counters, a histogram, the watcher's tick timestamps) —
  moved by the event methods below, at the place the event happens. Counters
  restart from zero with the process; ``rate()`` / ``increase()`` absorb that.
* **state metrics** (open cases, queue depth, cap occupancy) — gauges whose
  truth is in Postgres. They are only *declared* here; ``snapshot.py`` fills
  them on scrape.

An event method never raises: a metric must not be able to fail a tick, a
job transition or a sweep.

Cardinality: every label is an enum value or passes through
``labels.BoundedLabel``. Nothing is ever labelled by job, ticket, case, PR,
image or message.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, cast

import structlog
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from embry0.metrics.labels import BoundedLabel, ErrorLabels, enum_label, model_family, provider_label

logger = structlog.get_logger(__name__)

WATCHER_HEALTH_STATES = ("ok", "unverified", "invalid_logql", "missing_logql", "loki_misconfigured")
QUEUE_STATUSES = ("pending", "running", "awaiting_input", "paused")
UNASSIGNED_PIPELINE = "unassigned"

# A watcher analysis is minutes; a developer+review+QA pipeline is hours.
_JOB_DURATION_BUCKETS = (30, 60, 120, 300, 600, 1200, 1800, 3600, 7200, 14400, 28800)


def _never_raises[**P](method: Callable[P, None]) -> Callable[P, None]:
    @functools.wraps(method)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> None:
        try:
            method(*args, **kwargs)
        except Exception:
            logger.warning("loop_metric_failed", metric_event=method.__name__, exc_info=True)

    return guarded


def _epoch(moment: datetime | float | None) -> float:
    if moment is None:
        return time.time()
    return moment.timestamp() if isinstance(moment, datetime) else float(moment)


class LoopMetrics:
    """Every self-healing-loop metric, behind one method per event."""

    def __init__(
        self,
        registry: CollectorRegistry | None = None,
        *,
        categorise_error: Callable[[str | None], object] | None = None,
    ) -> None:
        self.registry = registry if registry is not None else CollectorRegistry()
        r = self.registry
        self._error_labels = ErrorLabels(categorise_error)
        self._pipeline = BoundedLabel(
            (UNASSIGNED_PIPELINE,), name="pipeline_template", limit=32, empty=UNASSIGNED_PIPELINE
        )
        self._reason = BoundedLabel(name="reason", limit=40)
        self._state_key = BoundedLabel(name="key", limit=80)
        self._section = BoundedLabel(name="section", limit=12)

        # ---- watcher ------------------------------------------------------
        # #378: `stack` is the watched repo. Without it two watchers write one
        # series and a stuck stack hides behind a busy one — the dead-man
        # alerts would read "something ticked" as "everything ticks".
        self._watcher_ticks = Counter(
            "embry0_watcher_ticks_total",
            "Watcher ticks recorded to watcher_runs, by action and watched stack.",
            ["action", "stack"],
            registry=r,
        )
        self._watcher_ticks_blocked = Counter(
            "embry0_watcher_ticks_blocked_total",
            "Scheduled ticks withheld because WATCHER_LOGQL is faulty (no watcher_runs row is written).",
            registry=r,
        )
        self._watcher_last_tick = Gauge(
            "embry0_watcher_last_tick_timestamp_seconds",
            "Unix time of the last recorded watcher tick for a stack, whatever its action. "
            "Seeded from watcher_runs at startup.",
            ["stack"],
            registry=r,
        )
        self._watcher_last_analysed = Gauge(
            "embry0_watcher_last_analysed_tick_timestamp_seconds",
            "Unix time of the last tick that analysed a stack's window "
            "(no_issue / proposed / deferred / skipped_duplicate / no_change). Seeded from watcher_runs at startup.",
            ["stack"],
            registry=r,
        )
        self._watcher_last_verdict = Gauge(
            "embry0_watcher_last_verdict_tick_timestamp_seconds",
            "Unix time of the last tick that reached a verdict on a stack: it analysed its window, or found "
            "it too quiet to need one (skipped_quiet). The dead-man alert's gauge. Seeded from watcher_runs "
            "at startup.",
            ["stack"],
            registry=r,
        )
        self._watcher_health = Gauge(
            "embry0_watcher_health",
            "1 for a stack's watcher's current /health/ready state, 0 for the others.",
            ["state", "stack"],
            registry=r,
        )
        self._watcher_enabled = Gauge(
            "embry0_watcher_enabled", "1 when the scheduled watcher loop is running (WATCHER_ENABLED).", registry=r
        )
        self._watcher_cost = Counter(
            "embry0_watcher_analysis_cost_usd_total",
            "USD spent on watcher analysis jobs, retries included.",
            registry=r,
        )
        self._watcher_unanalysed = Gauge(
            "embry0_watcher_unanalysed_seconds",
            "Seconds of log time the last tick's bounded window left out (0 = contiguous).",
            registry=r,
        )
        self._watcher_lost = Counter(
            "embry0_watcher_log_seconds_lost_total",
            "Seconds of log time skipped for good: an analysed tick moved the window past them.",
            registry=r,
        )
        self._watcher_truncated = Counter(
            "embry0_watcher_ticks_log_truncated_total",
            "Ticks whose Loki excerpt hit WATCHER_MAX_LOG_LINES, so the oldest matches were dropped. "
            "Counts ticks, not lines.",
            registry=r,
        )
        self._watcher_briefing_failures = Counter(
            "embry0_watcher_briefing_failures_total",
            "Briefing sections that could not be assembled for a tick, by section.",
            ["section"],
            registry=r,
        )
        # Per stack (#448). The cap is enforced per stack and a stack may
        # override it, so one number for all of them answers no question: a
        # sum compared against one stack's default fired with no stack capped
        # and stayed quiet with a small-cap stack blind.
        self.watcher_open_proposals = Gauge(
            "embry0_watcher_open_proposals",
            "Watcher drafts awaiting a human on a stack — the cases that stack's proposal cap counts.",
            ["stack"],
            registry=r,
        )
        self._watcher_open_proposals_cap = Gauge(
            "embry0_watcher_open_proposals_cap",
            "A stack's proposal cap: its max_open_proposals, else WATCHER_MAX_OPEN_PROPOSALS.",
            ["stack"],
            registry=r,
        )

        # ---- healing cases ------------------------------------------------
        self._case_events = Counter(
            "embry0_healing_case_events_total",
            "Healing-case events written to the ledger, by event and how it landed.",
            ["event", "effect"],
            registry=r,
        )
        self._cases_opened = Counter(
            "embry0_healing_cases_opened_total", "Healing cases opened, by origin.", ["origin"], registry=r
        )
        self._cases_resolved = Counter(
            "embry0_healing_cases_resolved_total",
            "Healing cases closed, by resolution path (embry0 vs the rest is the bypass rate).",
            ["resolution_path"],
            registry=r,
        )
        self.cases_open = Gauge(
            "embry0_healing_cases_open", "Open healing cases by stage and origin.", ["stage", "origin"], registry=r
        )
        self.cases_oldest_age = Gauge(
            "embry0_healing_oldest_open_case_age_seconds",
            "Age of the oldest open case in each stage, measured from when it entered the stage (0 = none).",
            ["stage"],
            registry=r,
        )
        self.cases_awaiting_verdict = Gauge(
            "embry0_healing_cases_awaiting_verdict",
            "Open cases whose fix is deployed (fix_deployed) and whose healed verdict is still pending.",
            registry=r,
        )
        self.cases_closed = Gauge(
            "embry0_healing_cases_closed",
            "All closed healing cases in the ledger, by resolution path.",
            ["resolution_path"],
            registry=r,
        )

        # ---- jobs ---------------------------------------------------------
        self._jobs_finished = Counter(
            "embry0_jobs_finished_total",
            "Jobs that reached a terminal status. error_code is 'none' when the job carries no code "
            "(every completed job); error_category is the failure taxonomy's own value, where 'unknown' "
            "means a missing or unrecognised code and 'uncategorised' means this build has no taxonomy "
            "to ask. Count failures with status='failed', not by category.",
            ["pipeline_template", "status", "error_code", "error_category"],
            registry=r,
        )
        self._job_duration = Histogram(
            "embry0_job_duration_seconds",
            "Wall time from a job's start (or creation) to its terminal status.",
            ["pipeline_template", "status"],
            buckets=_JOB_DURATION_BUCKETS,
            registry=r,
        )
        self._agent_spend = Counter(
            "embry0_agent_spend_usd_total",
            "USD spent by agent runs (one increment per trace row).",
            ["pipeline_template", "provider", "model_family"],
            registry=r,
        )
        self._job_event_persist_failures = Counter(
            "embry0_job_events_persist_failed_total",
            "Job events that could not be written to job_logs. Each was still published to live "
            "WebSocket clients but no reconnecting client can replay it (spec 3.5).",
            registry=r,
        )
        self._job_events_dropped = Counter(
            "embry0_job_events_dropped_total",
            "Job events dropped because the redaction scrub raised; the log line "
            "event_redaction_failed names the type.",
            registry=r,
        )
        self.jobs_in_flight = Gauge(
            "embry0_jobs_in_flight", "Non-terminal jobs by status (the queue).", ["status"], registry=r
        )

        # ---- linear dispatch reconciler -----------------------------------
        self._reconciler_sweeps = Counter(
            "embry0_linear_reconciler_sweeps_total",
            "Reconciler sweeps by mode and result (ok / disabled / error).",
            ["mode", "result"],
            registry=r,
        )
        self._reconciler_decisions = Counter(
            "embry0_linear_reconciler_decisions_total",
            "Per-ticket decisions, counted on every sweep that takes them "
            "(a ticket that stays listed is counted again each sweep).",
            ["decision", "reason"],
            registry=r,
        )
        self._reconciler_last_sweep_tickets = Gauge(
            "embry0_linear_reconciler_last_sweep_tickets",
            "Tickets in the last completed sweep, by decision.",
            ["decision"],
            registry=r,
        )
        self._reconciler_last_sweep = Gauge(
            "embry0_linear_reconciler_last_sweep_timestamp_seconds",
            "Unix time the last sweep completed.",
            registry=r,
        )
        self.reconciler_daily_dispatches = Gauge(
            "embry0_linear_reconciler_daily_dispatches",
            "Jobs the reconciler started in the last 24 h — what its daily cap counts.",
            registry=r,
        )
        self._reconciler_daily_cap = Gauge(
            "embry0_linear_reconciler_daily_dispatch_cap", "LINEAR_RECONCILE_MAX_DISPATCHES_PER_DAY.", registry=r
        )
        self._reconciler_enabled = Gauge(
            "embry0_linear_reconciler_enabled",
            "1 when the reconciler's sweep loop is running (LINEAR_RECONCILE_MODE is not off). "
            "Guard every reconciler alert with it: mode=off returns before a sweep, so the sweep "
            "gauges below stay at 0 and would otherwise read as a stuck sweep.",
            registry=r,
        )

        # ---- runner / infrastructure --------------------------------------
        self._stale_image = Counter(
            "embry0_sandbox_runner_stale_image_total",
            "Agent runs that met a sandbox image whose runner predates --config-stdin.",
            ["outcome"],
            registry=r,
        )
        self._state_folds = Counter(
            "embry0_state_channel_folded_concurrent_writes_total",
            "Concurrent same-superstep writes folded by a JobState channel (always a routing bug).",
            ["key", "channel"],
            registry=r,
        )
        self._reaper_volumes = Counter(
            "embry0_volume_reaper_volumes_total",
            "Volumes the reaper judged, by outcome (removed / would_remove / failed / kept) and reason.",
            ["outcome", "reason"],
            registry=r,
        )
        self._reaper_sweeps = Counter(
            "embry0_volume_reaper_sweeps_total",
            "Volume reaper sweeps by result (ok / list_failed / status_fetch_failed).",
            ["result"],
            registry=r,
        )

        # ---- settings imports --------------------------------------------
        # #165: seeded 0 at construction, so the series exists on the first
        # scrape and an alert on it can be told from one that never fires.
        self._background_enrollment_blocked = Gauge(
            "embry0_background_enrollment_blocked",
            "1 while the background enrollment import is held by a registered repository name no settings layer "
            "can be stored under (the Slop-O-Meter removes nothing meanwhile); 0 otherwise.",
            registry=r,
        )

        # ---- the DB snapshot itself ---------------------------------------
        self.snapshot_last_success = Gauge(
            "embry0_loop_snapshot_last_success_timestamp_seconds",
            "Unix time every DB-backed gauge was last refreshed without error.",
            registry=r,
        )
        self.snapshot_errors = Counter(
            "embry0_loop_snapshot_errors_total",
            "DB-backed gauge refreshes that failed or timed out, by source. The gauges keep their last value.",
            ["source"],
            registry=r,
        )

        # No stack is known at construction; each watcher seeds its own series
        # when it first reports (watcher_health).

    # ---- watcher ------------------------------------------------------------

    @_never_raises
    def watcher_tick(
        self,
        action: str,
        *,
        analysed: bool,
        analysis_cost_usd: float | None = None,
        unanalysed_seconds: int = 0,
        log_lines_truncated: bool = False,
        briefing_failures: list[str] | None = None,
        at: datetime | float | None = None,
        stack: str = "",
    ) -> None:
        """A tick was recorded to ``watcher_runs`` for ``stack``.

        ``analysed`` says the window was consumed: the analysed gauge and the
        lost-log accounting follow it. The verdict gauge is keyed on the
        action itself, not on a flag: a skipped_quiet tick is a verdict
        without being an analysis, and a flag the caller could forget to pass
        is exactly the silent failure #369 was.
        """
        from embry0.storage.repositories.watcher_runs import VERDICT_ACTIONS, WATCHER_ACTIONS

        self._watcher_ticks.labels(action=action if action in WATCHER_ACTIONS else "other", stack=stack).inc()
        moment = _epoch(at)
        self._watcher_last_tick.labels(stack=stack).set(moment)
        if analysed:
            self._watcher_last_analysed.labels(stack=stack).set(moment)
        if action in VERDICT_ACTIONS:
            self._watcher_last_verdict.labels(stack=stack).set(moment)
        if analysis_cost_usd and analysis_cost_usd > 0:
            self._watcher_cost.inc(analysis_cost_usd)
        self._watcher_unanalysed.set(max(unanalysed_seconds, 0))
        if analysed and unanalysed_seconds > 0:
            self._watcher_lost.inc(unanalysed_seconds)
        if log_lines_truncated:
            self._watcher_truncated.inc()
        for section in briefing_failures or ():
            self._watcher_briefing_failures.labels(section=self._section(section)).inc()

    @_never_raises
    def watcher_tick_blocked(self) -> None:
        self._watcher_ticks_blocked.inc()

    @_never_raises
    def watcher_health(self, state: str, stack: str = "") -> None:
        current = state if state in WATCHER_HEALTH_STATES else "unverified"
        for known in WATCHER_HEALTH_STATES:
            self._watcher_health.labels(state=known, stack=stack).set(1 if known == current else 0)

    @_never_raises
    def watcher_configured(self, *, enabled: bool, max_open_proposals: Mapping[str, int]) -> None:
        """``max_open_proposals`` is each watched stack's own cap, by stack."""
        self._watcher_enabled.set(1 if enabled else 0)
        for stack, cap in max_open_proposals.items():
            self._watcher_open_proposals_cap.labels(stack=stack).set(cap)

    @_never_raises
    def seed_watcher_ticks(
        self,
        *,
        last_tick_at: datetime | None,
        last_analysed_at: datetime | None,
        last_verdict_at: datetime | None,
        stack: str = "",
    ) -> None:
        """Startup: restore one stack's tick timestamps from ``watcher_runs``
        so a restart does not read as "never ticked" to a dead-man alert.
        Never moves a timestamp backwards. ``last_verdict_at`` is required,
        not defaulted: the dead-man reads that gauge, and a seed that quietly
        skipped it would re-arm the alert on every deploy after a quiet night."""
        for gauge, moment in (
            (self._watcher_last_tick, last_tick_at),
            (self._watcher_last_analysed, last_analysed_at),
            (self._watcher_last_verdict, last_verdict_at),
        ):
            series = gauge.labels(stack=stack)
            if moment is not None and moment.timestamp() > _value(series):
                series.set(moment.timestamp())

    # ---- healing cases ------------------------------------------------------

    @_never_raises
    def case_opened(self, origin: str) -> None:
        from embry0.services.healing.lifecycle import Origin

        self._cases_opened.labels(origin=enum_label(Origin, origin)).inc()

    @_never_raises
    def case_event(self, event: str, effect: str) -> None:
        from embry0.services.healing.lifecycle import CaseEvent, Effect

        self._case_events.labels(event=enum_label(CaseEvent, event), effect=enum_label(Effect, effect)).inc()

    @_never_raises
    def case_resolved(self, resolution_path: str | None) -> None:
        from embry0.services.healing.lifecycle import ResolutionPath

        self._cases_resolved.labels(resolution_path=enum_label(ResolutionPath, resolution_path)).inc()

    # ---- jobs ---------------------------------------------------------------

    @_never_raises
    def job_finished(
        self,
        *,
        pipeline_template: str | None,
        status: str,
        error_code: str | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        from embry0.storage.schemas import JobStatus

        pipeline = self._pipeline(pipeline_template)
        status_label = enum_label(JobStatus, status)
        code, category = self._error_labels(error_code)
        self._jobs_finished.labels(
            pipeline_template=pipeline, status=status_label, error_code=code, error_category=category
        ).inc()
        if duration_seconds is not None and duration_seconds >= 0:
            self._job_duration.labels(pipeline_template=pipeline, status=status_label).observe(duration_seconds)

    @_never_raises
    def agent_spend(self, *, pipeline_template: str | None, model: str | None, cost_usd: float | None) -> None:
        if not cost_usd or cost_usd <= 0:
            return
        self._agent_spend.labels(
            pipeline_template=self._pipeline(pipeline_template),
            provider=provider_label(model),
            model_family=model_family(model),
        ).inc(cost_usd)

    @_never_raises
    def job_event_persist_failed(self) -> None:
        self._job_event_persist_failures.inc()

    @_never_raises
    def job_event_dropped(self) -> None:
        """Job events dropped because the redaction scrub raised; the log line
        event_redaction_failed names the type."""
        self._job_events_dropped.inc()

    # ---- linear dispatch reconciler ------------------------------------------

    @_never_raises
    def reconciler_configured(self, *, enabled: bool, max_dispatches_per_day: int) -> None:
        self._reconciler_enabled.set(1 if enabled else 0)
        self._reconciler_daily_cap.set(max_dispatches_per_day)

    @_never_raises
    def reconciler_decision(self, decision: str, reason: str | None) -> None:
        self._reconciler_decisions.labels(decision=decision, reason=self._reason(reason)).inc()

    @_never_raises
    def reconciler_sweep(self, *, mode: str, result: str, tickets: dict[str, int] | None = None) -> None:
        self._reconciler_sweeps.labels(mode=mode, result=result).inc()
        if tickets is None:
            return
        for decision, count in tickets.items():
            self._reconciler_last_sweep_tickets.labels(decision=decision).set(count)
        self._reconciler_last_sweep.set(time.time())

    # ---- settings imports ---------------------------------------------------

    @_never_raises
    def background_enrollment_blocked(self, blocked: bool) -> None:
        """Whether the last run of the background enrollment import was held by an unaddressable name (#165)."""
        self._background_enrollment_blocked.set(1 if blocked else 0)

    # ---- runner / infrastructure -------------------------------------------

    @_never_raises
    def sandbox_runner_stale_image(self, outcome: str) -> None:
        """``argv_fallback`` (the run went ahead) or ``config_too_large`` (it could not)."""
        self._stale_image.labels(outcome=outcome).inc()

    @_never_raises
    def state_channel_fold(self, key: str, channel: str) -> None:
        self._state_folds.labels(key=self._state_key(key), channel=channel).inc()

    @_never_raises
    def volume_reaper_volume(self, outcome: str, reason: str) -> None:
        from embry0.cache.volume_ownership import VolumeReason

        self._reaper_volumes.labels(outcome=outcome, reason=enum_label(VolumeReason, reason)).inc()

    @_never_raises
    def volume_reaper_sweep(self, result: str) -> None:
        self._reaper_sweeps.labels(result=result).inc()


def _value(gauge: Gauge) -> float:
    return float(gauge._value.get())  # noqa: SLF001 — prometheus_client has no public read


def sample(registry: CollectorRegistry, name: str, labels: dict[str, str] | None = None) -> float | None:
    """Read one sample — for tests and the startup log, not for business logic."""
    return registry.get_sample_value(name, labels or {})


def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


class _SilentMetrics:
    """Stands in for ``LoopMetrics`` in a module built without one.

    Every attribute is the same no-op callable, so an event costs one lookup
    and allocates nothing. A real ``LoopMetrics`` builds ~34 collectors, and
    ``healing/backfill.py`` constructs one service per proposal — this is also
    why the default is a shared singleton rather than a fresh instance.

    It deliberately does not subclass ``LoopMetrics``: inheriting would run
    the real method bodies against missing collectors and log a warning per
    event. Only the event methods are honoured; the gauge attributes
    ``snapshot.py`` fills are not, and nothing hands it a null facade.
    """

    def __getattr__(self, _name: str) -> Callable[..., None]:
        return _noop


NULL_METRICS = cast("LoopMetrics", _SilentMetrics())
"""The default for every ``metrics=None`` producer. Records nothing, never raises."""


__all__ = ["NULL_METRICS", "QUEUE_STATUSES", "UNASSIGNED_PIPELINE", "WATCHER_HEALTH_STATES", "LoopMetrics", "sample"]
