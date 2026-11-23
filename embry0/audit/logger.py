"""Structured audit event logging — JSONL file + structlog."""

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from embry0.audit.actions import AuditAction

logger = structlog.get_logger("audit")

# Serializes JSONL appends against the retention sweeper's atomic rewrite
# (embry0.audit.retention). threading, not asyncio: this appender is sync.
_FILE_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def emit_audit_event(
    action: AuditAction,
    *,
    actor: str = "system",
    details: dict[str, Any] | None = None,
    audit_log_path: str | Path | None = None,
    issue_id: str | None = None,
) -> None:
    """Emit a structured audit event.

    Args:
        action: Security-relevant action, one of ``embry0.audit.actions.AuditAction``.
        actor: Who performed action (IP address, "system", "webhook").
        details: Action-specific key-value pairs.
        audit_log_path: Path to JSONL audit log file, as a ``Path`` or its text. None = skip file write.
        issue_id: Optional issue ID to associate the event with a specific issue.
    """
    event = {
        "timestamp": _now_iso(),
        "action": action,
        "actor": actor,
        "details": details or {},
        "issue_id": issue_id,
    }

    logger.info("audit_event", **event)

    if audit_log_path:
        # A caller may hold the path as text (a config value, a store's field): taken as a Path once, here.
        path = Path(audit_log_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with _FILE_LOCK, open(path, "a") as f:
                f.write(json.dumps(event, default=str) + "\n")
        except OSError as exc:
            logger.error("audit_log_write_failed", error=str(exc), path=str(path))
