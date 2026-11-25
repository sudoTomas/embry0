"""Close the traces of the agent runs the previous orchestrator process left ``running`` (#694).

A run's trace is written ``running`` before its exec and finished after it, so a run the process stopped in the middle
of (a restart, a deploy, a crash) keeps a ``running`` row at $0 for good: its job's spend leaves it out, and its page
shows it running. At start-up each such trace is closed with what is known of the run (``embry0.agents.run_close``):
the metering proxies' kept tallies, the sandbox's figure from the run's own events, or nothing, said so. The charge is
added to the job's total as the run's ``cost_update`` would have added it, in the same transaction as the close.

The traces to close are taken before any run of this process begins, so every one of them is the previous process's;
they are closed under a time budget at start-up and the rest in the background, each only while still ``running``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from embry0.agents.invocation import ProviderRecord
from embry0.agents.run_close import close_run, metering_from_kept, sandbox_figure

if TYPE_CHECKING:
    from embry0.storage.repositories.traces import TracesRepository

logger = structlog.get_logger(__name__)

#: The events of a run its sandbox's figure is read from; a tool_result marks where one model call ends.
FIGURE_EVENTS = ("cost_update", "turn_usage", "tool_result")
#: How long start-up spends closing them before it goes on and leaves the rest to the background.
STARTUP_BUDGET_SECONDS = 10.0


class KeptTallies(Protocol):
    async def for_run(self, run_id: str) -> list[dict[str, Any]]: ...


class RunEvents(Protocol):
    async def run_events(self, run_id: str, event_types: Any) -> list[dict[str, Any]]: ...


@dataclass
class Sweep:
    """What a sweep did within its budget, and what it left."""

    found: int = 0
    closed: int = 0
    #: The traces not reached before the budget ran out, for :func:`close_runs` to finish.
    left: list[dict[str, Any]] = field(default_factory=list)


async def close_interrupted_runs(
    traces: TracesRepository,
    tallies: KeptTallies | None,
    jobs: RunEvents,
    metrics: Any = None,
    *,
    budget_seconds: float | None = None,
) -> Sweep:
    """Take every trace still ``running`` and close them, for at most ``budget_seconds`` when given. Run at start-up
    only, before this process begins any run: every such trace is then one the previous process left. Never raises; a
    trace that cannot be closed is logged and left for the next start."""
    try:
        running = await traces.running()
    except Exception:
        logger.warning("interrupted_runs_scan_failed", exc_info=True)
        return Sweep()
    deadline = time.monotonic() + budget_seconds if budget_seconds is not None else None
    sweep = Sweep(found=len(running))
    for index, row in enumerate(running):
        if deadline is not None and time.monotonic() >= deadline:
            sweep.left = running[index:]
            break
        sweep.closed += await _close_logged(row, traces, tallies, jobs, metrics)
    if running:
        logger.info("interrupted_runs_closed", found=sweep.found, closed=sweep.closed, left=len(sweep.left))
    return sweep


async def close_runs(
    rows: list[dict[str, Any]], traces: TracesRepository, tallies: KeptTallies | None, jobs: RunEvents, metrics: Any
) -> int:
    """Close ``rows`` (what a sweep left), each only while still ``running``. Never raises."""
    closed = 0
    for row in rows:
        closed += await _close_logged(row, traces, tallies, jobs, metrics)
    if rows:
        logger.info("interrupted_runs_closed_after_start", found=len(rows), closed=closed)
    return closed


async def _close_logged(
    row: dict[str, Any], traces: TracesRepository, tallies: KeptTallies | None, jobs: RunEvents, metrics: Any
) -> int:
    try:
        return await _close(row, traces, tallies, jobs, metrics)
    except Exception:
        logger.warning("interrupted_run_close_failed", trace_id=row.get("trace_id"), exc_info=True)
        return 0


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
    update = None
    if closing.delta_usd > 0:
        node = next((str(e["node"]) for e in events if e.get("node")), str(row.get("agent_type") or ""))
        update = closing.cost_update(run_id=run_id, node=node, agent=str(row.get("agent_type") or ""))
    if not await traces.close_running(run_id, **closing.fields, cost_update=update):
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
    return 1
