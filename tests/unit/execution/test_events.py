import json

from legion.execution.events import (
    AgentCompletedEvent,
    AgentStartedEvent,
    EventType,
    GithubApiEvent,
    GitOperationEvent,
    ProgressEvent,
    ToolCallEvent,
    parse_event,
    serialize_event,
)


def test_serialize_agent_started():
    event = AgentStartedEvent(agent="developer")
    line = serialize_event(event)
    data = json.loads(line)
    assert data["type"] == "agent_started"
    assert data["agent"] == "developer"
    assert "timestamp" in data


def test_serialize_progress():
    event = ProgressEvent(message="Reading src/main.py")
    line = serialize_event(event)
    data = json.loads(line)
    assert data["type"] == "progress"
    assert data["message"] == "Reading src/main.py"


def test_serialize_tool_call():
    event = ToolCallEvent(tool="Edit", file="src/main.py")
    line = serialize_event(event)
    data = json.loads(line)
    assert data["type"] == "tool_call"
    assert data["tool"] == "Edit"


def test_serialize_agent_completed():
    event = AgentCompletedEvent(
        result={"passed": True},
        cost_usd=0.42,
        duration_ms=15000,
        tools_called={"Read": 5, "Edit": 2},
    )
    line = serialize_event(event)
    data = json.loads(line)
    assert data["type"] == "agent_completed"
    assert data["cost_usd"] == 0.42
    assert data["tools_called"]["Read"] == 5


def test_serialize_git_operation():
    event = GitOperationEvent(op="commit", message="fix: auth bug")
    line = serialize_event(event)
    data = json.loads(line)
    assert data["type"] == "git_operation"
    assert data["op"] == "commit"


def test_serialize_github_api():
    event = GithubApiEvent(op="create_pr", pr_url="https://github.com/o/r/pull/1")
    line = serialize_event(event)
    data = json.loads(line)
    assert data["type"] == "github_api"
    assert data["pr_url"] == "https://github.com/o/r/pull/1"


def test_parse_event_valid():
    line = '{"type": "progress", "message": "hello", "timestamp": "2026-01-01T00:00:00+00:00"}'
    event = parse_event(line)
    assert event is not None
    assert event["type"] == "progress"


def test_parse_event_invalid_json():
    event = parse_event("not json")
    assert event is None


def test_parse_event_empty_line():
    event = parse_event("")
    assert event is None


def test_event_type_values():
    assert EventType.AGENT_STARTED == "agent_started"
    assert EventType.AGENT_COMPLETED == "agent_completed"
    assert EventType.PROGRESS == "progress"
    assert EventType.TOOL_CALL == "tool_call"
    assert EventType.GIT_OPERATION == "git_operation"
    assert EventType.GITHUB_API == "github_api"
    assert EventType.ERROR == "error"
