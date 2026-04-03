import json
from unittest.mock import MagicMock

from legion.execution.agent_runner import AgentOutput, AgentRunner


def test_agent_output_dataclass():
    output = AgentOutput(
        agent_type="developer",
        is_error=False,
        output="Fixed the bug",
        cost_usd=0.42,
        duration_ms=15000,
        tools_called={"Read": 5, "Edit": 2},
    )
    assert output.agent_type == "developer"
    assert output.cost_usd == 0.42
    assert not output.is_error


def test_parse_event_stream():
    """AgentRunner parses JSON lines from stdout."""
    runner = AgentRunner(sandbox_manager=MagicMock(), docker=MagicMock())

    events = [
        {"type": "agent_started", "agent": "developer", "timestamp": "t1"},
        {"type": "progress", "message": "Reading files", "timestamp": "t2"},
        {"type": "agent_completed", "cost_usd": 0.5, "duration_ms": 10000, "timestamp": "t3"},
        {
            "type": "final_result",
            "agent_type": "developer",
            "is_error": False,
            "output": "done",
            "cost_usd": 0.5,
            "duration_ms": 10000,
            "tools_called": {},
        },
    ]
    lines = [json.dumps(e) + "\n" for e in events]

    collected: list[dict] = []

    result = runner._parse_stdout_events(lines, on_event=collected.append)

    assert result is not None
    assert result["agent_type"] == "developer"
    assert result["cost_usd"] == 0.5
    assert len(collected) == 3  # started + progress + completed (not final_result)


def test_parse_handles_invalid_json():
    """Invalid JSON lines are skipped."""
    runner = AgentRunner(sandbox_manager=MagicMock(), docker=MagicMock())

    lines = [
        "not json\n",
        '{"type": "progress", "message": "ok"}\n',
        json.dumps({"type": "final_result", "agent_type": "dev", "is_error": False,
                    "output": "", "cost_usd": 0, "duration_ms": 0, "tools_called": {}}) + "\n",
    ]

    result = runner._parse_stdout_events(lines, on_event=lambda _: None)
    assert result is not None
