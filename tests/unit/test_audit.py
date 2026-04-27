import json
from pathlib import Path

from athanor.audit.logger import emit_audit_event


def test_emit_audit_event_to_file(tmp_path: Path):
    """Audit events are appended to JSONL file."""
    log_path = tmp_path / "audit.jsonl"
    emit_audit_event("job_created", actor="test", details={"job_id": "j-1"}, audit_log_path=log_path)
    emit_audit_event("job_completed", actor="test", details={"job_id": "j-1"}, audit_log_path=log_path)

    lines = log_path.read_text().strip().split("\n")
    assert len(lines) == 2

    event = json.loads(lines[0])
    assert event["action"] == "job_created"
    assert event["actor"] == "test"
    assert event["details"]["job_id"] == "j-1"
    assert "timestamp" in event


def test_emit_audit_event_without_file():
    """Audit events work without file path (structlog only)."""
    # Should not raise
    emit_audit_event("config_updated", details={"key": "max_budget_usd"})


def test_emit_audit_event_creates_parent_dirs(tmp_path: Path):
    """Audit log creates parent directories if needed."""
    log_path = tmp_path / "nested" / "dir" / "audit.jsonl"
    emit_audit_event("test_action", audit_log_path=log_path)
    assert log_path.exists()
