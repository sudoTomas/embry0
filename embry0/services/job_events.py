"""Job events: the one path from a producer to job_logs and the WebSocket bus.

``broadcast_job_event`` stamps ``job_id``, ``timestamp`` and ``prev_seq`` (the
job's previous persisted ``event_seq``, remembered per live job in this
process), persists the event (the row id becomes ``event_seq``), adds a
``cost_update`` delta to ``jobs.total_cost_usd`` and publishes to the bus. ``IssueExecutor``
delegates its ``_broadcast_event`` here; the paused-job reaper and the boot
orphan sweep, which have no executor, call it directly (they pass
``bus=None`` when nothing can be subscribed yet, so the event is persisted
and replayed, not lost).

``emit_job_finished`` is spec 3.5's terminal event: after a terminal status
write, read the job row and broadcast ``job_finished`` built from it. The
row is the one source every site has in scope; a row that is not terminal is
never announced.

Both functions are best-effort: a failure here is logged and must not fail
the job transition that called them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

from embry0.execution import events
from embry0.safety.redaction import redact_event, secret_env_values
from embry0.storage.repositories.jobs import TERMINAL_JOB_STATUSES

logger = structlog.get_logger(__name__)

#: The last persisted ``event_seq`` of every live job. ``event_seq`` is
#: ``job_logs.id``, shared by every job, so consecutive events of one job are
#: not contiguous; stamping the previous one as ``prev_seq`` is what lets a
#: client tell a gap from another job's interleaved ids (spec 3.5). An entry
#: is dropped once the job's ``job_finished`` has gone out.
_LAST_SEQ: dict[str, int] = {}


def reset_sequence_memory() -> None:
    """Forget every job's last persisted seq (tests)."""
    _LAST_SEQ.clear()


class EventPublisher(Protocol):
    async def publish(self, job_id: str, event: dict[str, Any]) -> None: ...


class JobEventStore(Protocol):
    async def append_log_event(self, job_id: str, event: dict[str, Any]) -> int: ...
    async def increment_cost(self, job_id: str, cost_delta: float) -> None: ...
    async def get(self, job_id: str) -> dict[str, Any] | None: ...


async def broadcast_job_event(
    jobs: JobEventStore,
    bus: EventPublisher | None,
    metrics: Any,
    job_id: str,
    event: dict[str, Any],
) -> dict[str, Any] | None:
    """Stamp, persist, count and publish one event. Returns the event as
    published (with ``event_seq`` when the persist succeeded, and ``prev_seq``
    when this job had an earlier event persisted by this process)."""
    event = {**event, "job_id": job_id}
    if "timestamp" not in event:
        event = {**event, "timestamp": datetime.now(UTC).isoformat()}
    prev = _LAST_SEQ.get(job_id)
    if prev is not None:
        event = {**event, "prev_seq": prev}

    # spec 3.6: redact before persisting and before publishing, every string
    # value, recursively. spec 3.9: a scrubber that raises drops the event;
    # the log carries the type only, never the payload.
    try:
        event = redact_event(event, secret_env_values())
    except Exception:
        logger.error("event_redaction_failed", job_id=job_id, event_type=str(event.get("type") or "unknown"))
        if metrics is not None:
            metrics.job_event_dropped()
        return None

    seq: int | None = None
    try:
        seq = await jobs.append_log_event(job_id, event)
    except Exception:
        # Published anyway (a live client still sees it) but no client can
        # replay it; the counter is what makes that visible (spec 3.5).
        logger.warning("event_persist_failed", job_id=job_id, event_type=event.get("type"), exc_info=True)
        if metrics is not None:
            metrics.job_event_persist_failed()
    if seq is not None:
        event = {**event, "event_seq": seq}
        _LAST_SEQ[job_id] = seq

    # cost_update carries the run's delta; it is the only event that moves
    # jobs.total_cost_usd (spec 3.3: turn_usage is an estimate, never added).
    if event.get("type") == "cost_update" and event.get("cost_usd"):
        try:
            await jobs.increment_cost(job_id, event["cost_usd"])
        except Exception:
            logger.warning("cost_update_failed", job_id=job_id, exc_info=True)

    if bus is not None:
        await bus.publish(job_id, event)
    if event.get("type") == "job_finished":
        _LAST_SEQ.pop(job_id, None)  # the job is over: keep the map bounded by live jobs
    return event


async def emit_job_finished(jobs: JobEventStore, bus: EventPublisher | None, metrics: Any, job_id: str) -> None:
    """Broadcast ``job_finished`` for a job whose terminal status was just written.

    Reads the row rather than taking fields, because no two terminal sites
    have the same fields in scope (cost is resolved inline in one, pr_url is
    deliberately absent in another). Never raises.
    """
    try:
        row = await jobs.get(job_id)
    except Exception:
        logger.warning("job_finished_event_row_fetch_failed", job_id=job_id, exc_info=True)
        return
    if not row:
        logger.warning("job_finished_event_no_row", job_id=job_id)
        return
    status = str(row.get("status") or "")
    if status not in TERMINAL_JOB_STATUSES:
        logger.warning("job_finished_event_skipped_non_terminal", job_id=job_id, status=status)
        return
    try:
        await broadcast_job_event(
            jobs,
            bus,
            metrics,
            job_id,
            events.job_finished(
                status=status,
                error_code=row.get("error_code"),
                error_message=row.get("error_message"),
                pr_url=row.get("pr_url"),
                total_cost_usd=float(row.get("total_cost_usd") or 0.0),
            ),
        )
    except Exception:
        logger.warning("job_finished_event_failed", job_id=job_id, exc_info=True)
