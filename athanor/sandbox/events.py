"""Stdout event emission from inside the sandbox container."""

import json
import sys
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    THINKING = "thinking"
    TEXT = "text"
    TURN_START = "turn_start"
    COST_UPDATE = "cost_update"
    PROGRESS = "progress"
    ERROR = "error"
    AGENT_ASK_USER = "agent_ask_user"
    GITHUB_API = "github_api"


def emit_event(event_type: str, **kwargs: Any) -> None:
    """Write a JSON event line to stdout."""
    event: dict[str, Any] = {
        "type": event_type,
        "timestamp": datetime.now(UTC).isoformat(),
        **kwargs,
    }
    sys.stdout.write(json.dumps(event, default=str) + "\n")
    sys.stdout.flush()
