"""Close the traces of the agent runs the previous orchestrator process left ``running`` (#694).

A run's trace is written ``running`` before its exec and finished after it, so a run the process stopped in the middle
of (a restart, a deploy, a crash) keeps a ``running`` row at $0 for good: its job's spend leaves it out, and its page
shows it running. At start-up, before any run of this process begins, each such trace is closed with what is known of
the run (``embry0.agents.run_close``): the metering proxies' kept tallies, the sandbox's figure from the run's own
events, or nothing, said so. The charge is added to the job's total as the run's ``cost_update`` would have added it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from embry0.agents.invocation import ProviderRecord
from embry0.agents.run_close import close_run, metering_from_kept, sandbox_figure
from embry0.services.job_events import JobEventStore, broadcast_job_event

if TYPE_CHECKING:
    from embry0.storage.repositories.traces import TracesRepository

logger = structlog.get_logger(__name__)

#: The events of a run its sandbox's figure is read from.
FIGURE_EVENTS = ("cost_update", "turn_usage")


class KeptTallies(Protocol):
    async def for_run(self, run_id: str) -> list[dict[str, Any]]: ...


class RunEvents(JobEventStore, Protocol):
    async def run_events(self, run_id: str, event_types: Any) -> list[dict[str, Any]]: ...


async def close_interrupted_runs(
    traces: TracesRepository, tallies: KeptTallies | None, jobs: RunEvents, metrics: Any = None
) -> int:
    """Close every trace still ``running``. Run at start-up only, before this process begins any run: every such trace
    is then one the previous process left. Never raises; a trace that cannot be closed is logged and left for the next
    start. Returns how many were closed."""
    try:
        running = await traces.running()
    except Exception:
        logger.warning("interrupted_runs_scan_failed", exc_info=True)
        return 0
    closed = 0
    for row in running:
        try:
            closed += await _close(row, traces, tallies, jobs, metrics)
        except Exception:
            logger.warning("interrupted_run_close_failed", trace_id=row.get("trace_id"), exc_info=True)
    if running:
        logger.info("interrupted_runs_closed", found=len(running), closed=closed)
    return closed


async def _close(
    row: dict[str, Any], traces: TracesRepository, tallies: KeptTallies | None, jobs: RunEvents, metrics: Any
) -> int:
    run_id, job_id = str(row["trace_id"]), str(row["job_id"])
    record = ProviderRecord.from_trace_pricing(row.get("pricing"))
    events = await jobs.run_events(run_id, FIGURE_EVENTS)
    kept = await tallies.for_run(run_id) if tallies is not None else []
    figure = sandbox_figure(events, record)
    started = row.get("created_at")
    duration_ms = (datetime.now(UTC) - started).total_seconds() * 1000 if isinstance(started, datetime) else 0
    closing = close_run(
        cause="restart",
        record=record,
        metering=metering_from_kept(run_id, kept),
        figure=figure,
        duration_ms=int(duration_ms),
    )
    if not await traces.close_running(run_id, **closing.fields):
        return 0
    if metrics is not None:
        metrics.run_closed("restart", closing.charged_from)
    log = logger.warning if closing.charged_from == "nothing" else logger.info
    log(
        "interrupted_run_closed",
        trace_id=run_id,
        job_id=job_id,
        cause="restart",
        charged_from=closing.charged_from,
        cost_usd=closing.fields["cost_usd"],
    )
    if closing.delta_usd > 0:
        node = next((str(e["node"]) for e in events if e.get("node")), str(row.get("agent_type") or ""))
        update = closing.cost_update(run_id=run_id, node=node, agent=str(row.get("agent_type") or ""))
        await broadcast_job_event(jobs, None, metrics, job_id, update)
    return 1
