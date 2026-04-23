"""sandbox/runner.py — deserializes config JSON and delegates to SdkAgentExecutor."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from legion.execution.agent_runner import AgentOutput
from legion.sandbox.events import EventType, emit_event
from legion.sandbox.runner import _invocation_from_config, run_agent


# ---------------------------------------------------------------------------
# Preserved: wire-format event emission is still stdout JSON-per-line.
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# New: _invocation_from_config deserializes every field plumbed from the
# orchestrator into an AgentInvocation.
# ---------------------------------------------------------------------------
def test_invocation_from_config_builds_all_fields() -> None:
    cfg = {
        "agent_type": "developer",
        "prompt": "hi",
        "system_prompt": "sp",
        "system_context": "sc",
        "model": "claude-sonnet-4-6",
        "tools": ["Read"],
        "skills": ["s1"],
        "mcp_servers": {"m": {"cmd": "x"}},
        "max_turns": 5,
        "timeout_seconds": 30,
        "execution_mode": "sdk",
        "auth_mode": "api_key",
    }
    inv = _invocation_from_config(cfg)
    assert inv.agent_type == "developer"
    assert inv.prompt == "hi"
    assert inv.system_prompt == "sp"
    assert inv.system_context == "sc"
    assert inv.model == "claude-sonnet-4-6"
    assert inv.tools == ["Read"]
    assert inv.skills == ["s1"]
    assert inv.mcp_servers == {"m": {"cmd": "x"}}
    assert inv.max_turns == 5
    assert inv.timeout_seconds == 30
    assert inv.execution_mode == "sdk"
    assert inv.auth_mode == "api_key"
    # Safety policy is populated from the agent_type; developer gets the
    # common developer toolbelt.
    assert "Read" in inv.safety_policy.allowed_tools


def test_invocation_from_config_defaults_when_fields_missing() -> None:
    """Only `prompt` is strictly required; the rest have safe defaults."""
    inv = _invocation_from_config({"prompt": "ping"})
    assert inv.agent_type == "agent"
    assert inv.system_prompt == ""
    assert inv.system_context == ""
    assert inv.tools == []
    assert inv.skills == []
    assert inv.mcp_servers == {}
    assert inv.execution_mode == "sdk"
    assert inv.auth_mode == "oauth"


# ---------------------------------------------------------------------------
# New: run_agent delegates to the executor chosen by select_executor and
# returns the executor's AgentOutput as a dict.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_agent_delegates_to_executor() -> None:
    fake_result = AgentOutput(
        agent_type="developer",
        is_error=False,
        output="done",
        cost_usd=0.05,
        duration_ms=42,
        tools_called={"Read": 1},
    )
    mock_executor = AsyncMock()
    mock_executor.run = AsyncMock(return_value=fake_result)

    with patch("legion.sandbox.runner.select_executor", return_value=mock_executor):
        out = await run_agent(
            {
                "agent_type": "developer",
                "prompt": "go",
                "system_prompt": "",
                "system_context": "",
                "model": "claude-sonnet-4-6",
                "tools": ["Read"],
                "execution_mode": "sdk",
                "auth_mode": "oauth",
            }
        )

    assert out["is_error"] is False
    assert out["output"] == "done"
    assert out["cost_usd"] == pytest.approx(0.05)
    assert out["duration_ms"] == 42
    assert out["tools_called"] == {"Read": 1}
    # Executor received the deserialized invocation and a writer-bearing config.
    call = mock_executor.run.await_args
    invocation_arg, config_arg = call.args
    assert invocation_arg.agent_type == "developer"
    assert invocation_arg.prompt == "go"
    assert "_test_writer" in config_arg
