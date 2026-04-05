"""Stdout event protocol — JSON lines between sandbox and orchestrator.

Each line is a JSON object with a "type" field and a "timestamp" field.
The sandbox runner emits events; the agent runner parses them.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class EventType(StrEnum):
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    PROGRESS = "progress"
    TOOL_CALL = "tool_call"
    GIT_OPERATION = "git_operation"
    GITHUB_API = "github_api"
    ERROR = "error"


@dataclass
class AgentStartedEvent:
    agent: str
    type: str = EventType.AGENT_STARTED
    timestamp: str = ""


@dataclass
class AgentCompletedEvent:
    result: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    duration_ms: int = 0
    tools_called: dict[str, int] = field(default_factory=dict)
    type: str = EventType.AGENT_COMPLETED
    timestamp: str = ""


@dataclass
class ProgressEvent:
    message: str = ""
    type: str = EventType.PROGRESS
    timestamp: str = ""


@dataclass
class ToolCallEvent:
    tool: str = ""
    file: str = ""
    type: str = EventType.TOOL_CALL
    timestamp: str = ""


@dataclass
class GitOperationEvent:
    op: str = ""
    message: str = ""
    type: str = EventType.GIT_OPERATION
    timestamp: str = ""


@dataclass
class GithubApiEvent:
    op: str = ""
    pr_url: str = ""
    type: str = EventType.GITHUB_API
    timestamp: str = ""


@dataclass
class ErrorEvent:
    error: str = ""
    type: str = EventType.ERROR
    timestamp: str = ""


_EventT = (
    AgentStartedEvent
    | AgentCompletedEvent
    | ProgressEvent
    | ToolCallEvent
    | GitOperationEvent
    | GithubApiEvent
    | ErrorEvent
)


def serialize_event(event: _EventT) -> str:
    """Serialize an event dataclass to a JSON line."""
    data = asdict(event)
    if not data.get("timestamp"):
        data["timestamp"] = datetime.now(UTC).isoformat()
    return json.dumps(data, default=str)


def parse_event(line: str) -> dict[str, Any] | None:
    """Parse a JSON line into an event dict. Returns None on parse failure."""
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        logger.warning("event_parse_failed", line=line[:200])
        return None
