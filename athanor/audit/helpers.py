"""Unified audit event emission — writes to both file and database."""

from __future__ import annotations

from typing import Any

from athanor.audit.db_logger import emit_audit_event_db
from athanor.audit.logger import emit_audit_event
from athanor.storage.database import DatabasePool


async def emit_audit(
    db: DatabasePool | None,
    action: str,
    *,
    actor: str = "system",
    details: dict[str, Any] | None = None,
    audit_log_path: Any = None,
    issue_id: str | None = None,
) -> None:
    """Emit an audit event to both file and database."""
    emit_audit_event(
        action,
        actor=actor,
        details=details,
        audit_log_path=audit_log_path,
        issue_id=issue_id,
    )
    if db is not None:
        await emit_audit_event_db(
            db,
            action,
            actor=actor,
            details=details,
            issue_id=issue_id,
        )
