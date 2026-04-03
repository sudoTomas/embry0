import json

from legion.sandbox.events import EventType, emit_event


def test_emit_event_stdout(capsys):
    emit_event(EventType.PROGRESS, message="Reading files")
    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())
    assert data["type"] == "progress"
    assert data["message"] == "Reading files"
    assert "timestamp" in data


def test_emit_agent_started(capsys):
    emit_event(EventType.AGENT_STARTED, agent="developer")
    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())
    assert data["type"] == "agent_started"
    assert data["agent"] == "developer"


def test_emit_agent_completed(capsys):
    emit_event(
        EventType.AGENT_COMPLETED,
        cost_usd=0.42,
        duration_ms=15000,
        tools_called={"Read": 5},
        result={"passed": True},
    )
    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())
    assert data["cost_usd"] == 0.42
    assert data["tools_called"]["Read"] == 5
