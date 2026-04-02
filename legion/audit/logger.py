"""Structured audit event logging — JSONL file + structlog."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger("audit")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def emit_audit_event(
    action: str,
    *,
    actor: str = "system",
    details: dict[str, Any] | None = None,
    audit_log_path: Path | None = None,
) -> None:
    """Emit a structured audit event.

    Args:
        action: Security-relevant action (e.g. "config_updated", "job_created").
        actor: Who performed action (IP address, "system", "webhook").
        details: Action-specific key-value pairs.
        audit_log_path: Path to JSONL audit log file. None = skip file write.
    """
    event = {
        "timestamp": _now_iso(),
        "action": action,
        "actor": actor,
        "details": details or {},
    }

    logger.info("audit_event", **event)

    if audit_log_path:
        try:
            audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(audit_log_path, "a") as f:
                f.write(json.dumps(event, default=str) + "\n")
        except OSError as exc:
            logger.error("audit_log_write_failed", error=str(exc), path=str(audit_log_path))
