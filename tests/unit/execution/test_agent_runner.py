import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

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


@pytest.mark.asyncio
async def test_run_times_out_and_kills_process():
    """AgentRunner.run() times out and kills the process when it hangs."""
    sandbox = MagicMock()
    sandbox.connect_network = AsyncMock()
    sandbox.disconnect_network = AsyncMock()

    docker = MagicMock()

    # Simulate a proc that never produces output (hangs forever).
    async def hanging_stdout():
        await asyncio.sleep(9999)
        return
        yield  # Make it an async generator.

    proc = MagicMock()
    proc.stdout = hanging_stdout()
    proc.wait = AsyncMock(side_effect=lambda: asyncio.sleep(9999))
    proc.kill = MagicMock()
    docker.stream_exec = AsyncMock(return_value=proc)

    runner = AgentRunner(sandbox_manager=sandbox, docker=docker)
    config = {
        "agent_type": "developer",
        "prompt": "do work",
        "timeout_seconds": 0.05,  # 50ms — fires almost immediately.
    }

    result = await runner.run(container="test-container", config=config)

    assert result.is_error is True
    assert "timed out" in result.error_message
    proc.kill.assert_called_once()
