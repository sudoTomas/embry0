"""Database-backed audit event logging."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from embry0.storage.database import DatabasePool

logger = structlog.get_logger("audit")


async def emit_audit_event_db(
    db: DatabasePool,
    action: str,
    *,
    actor: str = "system",
    details: dict[str, Any] | None = None,
    issue_id: str | None = None,
) -> None:
    """Write an audit event to the audit_log database table.

    If a `trace_id` is bound to structlog contextvars (see _run_workflow), it
    is read from there and stored on the row — giving ops a stable grep key
    to correlate events across the whole issue → triage → dev → review span.
    """
    import structlog.contextvars as cv

    trace_id = cv.get_contextvars().get("trace_id")
    try:
        await db.execute(
            """
            INSERT INTO audit_log (action, actor, details, issue_id, trace_id, created_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            action,
            actor,
            details or {},
            issue_id,
            trace_id,
            datetime.now(UTC),
        )
    except Exception as exc:
        logger.error("audit_db_write_failed", action=action, error=str(exc))
