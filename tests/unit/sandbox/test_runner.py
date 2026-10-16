"""sandbox/runner.py — deserializes config JSON and delegates to SdkAgentExecutor."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from embry0.execution import events
from embry0.execution.agent_runner import AgentOutput
from embry0.execution.events import emit_to_stdout
from embry0.sandbox.runner import _invocation_from_config, main, run_agent


# ---------------------------------------------------------------------------
# Preserved: wire-format event emission is still stdout JSON-per-line.
# ---------------------------------------------------------------------------
def test_emit_event_stdout(capsys):
    emit_to_stdout(events.progress(message="Reading files"))
    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())
    assert data["type"] == "progress"
    assert data["message"] == "Reading files"
    assert "timestamp" in data


def test_emit_agent_started(capsys):
    emit_to_stdout(events.agent_started(model="m", max_turns=1, run_id="r", node="developer", agent="developer"))
    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())
    assert data["type"] == "agent_started"
    assert data["agent"] == "developer"


def test_emit_agent_completed(capsys):
    emit_to_stdout(
        events.agent_completed(
            is_error=False,
            error_code=None,
            error_message="",
            output="",
            cost_usd=0.42,
            duration_ms=1,
            tools_called={"Read": 5},
            policy_denials={},
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            run_id="r",
            node="developer",
            agent="developer",
        )
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
        "run_id": "run-1",
        "node": "developer",
    }
    inv = _invocation_from_config(cfg)
    assert inv.run_id == "run-1"
    assert inv.node == "developer"
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
    assert inv.run_id == ""
    assert inv.node == ""


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

    with patch("embry0.sandbox.runner.select_executor", return_value=mock_executor):
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
    # EMB-35: token fields always present on the wire dict (0 when unset).
    assert out["input_tokens"] == 0
    assert out["output_tokens"] == 0
    assert out["cache_read_tokens"] == 0
    assert out["cache_creation_tokens"] == 0


@pytest.mark.asyncio
async def test_run_agent_forwards_the_provider_error_code() -> None:
    """A typed 429 whose message has no rate-limit needle must still leave the sandbox."""
    fake_result = AgentOutput(
        agent_type="developer",
        is_error=True,
        error_message="nope",
        error_code="ERR_PROVIDER_RATE_LIMITED",
    )
    mock_executor = AsyncMock()
    mock_executor.run = AsyncMock(return_value=fake_result)

    with patch("embry0.sandbox.runner.select_executor", return_value=mock_executor):
        out = await run_agent(
            {
                "agent_type": "developer",
                "prompt": "go",
                "model": "grok-4.5",
                "execution_mode": "sdk",
                "auth_mode": "oauth",
            }
        )

    assert out.get("error_code") == "ERR_PROVIDER_RATE_LIMITED", (
        "the sandbox wire must carry ERR_PROVIDER_RATE_LIMITED so the outcome hook can quarantine"
    )
    # Executor received the deserialized invocation and a writer-bearing config.
    call = mock_executor.run.await_args
    invocation_arg, config_arg = call.args
    assert invocation_arg.agent_type == "developer"
    assert invocation_arg.prompt == "go"
    assert "_test_writer" in config_arg


def test_invocation_syncs_policy_allowlist_with_config_tools() -> None:
    """EMB-37: the Ring-3 name check enforces policy.allowed_tools, so the
    policy MUST carry the invocation's real tools (the per-agent-type default
    map has no 'qa' entry and would brick MCP-heavy agents)."""
    cfg = {
        "agent_type": "qa",
        "prompt": "hi",
        "tools": ["Read", "Bash", "mcp__playwright__browser_navigate"],
    }
    inv = _invocation_from_config(cfg)
    assert inv.safety_policy.allowed_tools == ["Read", "Bash", "mcp__playwright__browser_navigate"]


def test_invocation_without_tools_keeps_agent_type_default_policy() -> None:
    inv = _invocation_from_config({"prompt": "ping", "agent_type": "review"})
    assert inv.safety_policy.allowed_tools == ["Read", "Glob", "Grep", "Bash"]


def test_is_error_run_writes_a_redacted_diagnostic(monkeypatch, capsys) -> None:
    """Exit 1 used to leave stderr empty. The diagnosis has to be on stderr and
    on final_result, with secrets already scrubbed — the orchestrator log is
    not the only place this line is read."""
    token = "sk-" + "b" * 24

    async def fake_run(**_kwargs):
        return {
            "agent_type": "triage",
            "is_error": True,
            "error_message": f"API Error: 529 overloaded Authorization: Bearer {token}",
            "error_code": None,
            "output": f"partial {token}",
        }

    monkeypatch.setattr("embry0.sandbox.runner._read_config", lambda argv=None: ({"prompt": "x"}, None, None))
    monkeypatch.setattr("embry0.sandbox.runner.run_agent", fake_run)

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert token not in captured.err
    assert "529" in captured.err
    assert "error_message=" in captured.err
    assert "result=" in captured.err
    line = json.loads(captured.out.strip().splitlines()[-1])
    assert line["type"] == "final_result"
    assert line["is_error"] is True
    assert token not in line["diagnostic"]
    assert "529" in line["diagnostic"]
    # Job-facing fields stay intact so the outcome hook can still classify them.
    assert token in line["error_message"]


def test_successful_run_writes_no_diagnostic(monkeypatch, capsys) -> None:
    async def fake_run(**_kwargs):
        return {"agent_type": "triage", "is_error": False, "error_message": "", "output": "done"}

    monkeypatch.setattr("embry0.sandbox.runner._read_config", lambda argv=None: ({"prompt": "x"}, None, None))
    monkeypatch.setattr("embry0.sandbox.runner.run_agent", fake_run)

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    line = json.loads(captured.out.strip().splitlines()[-1])
    assert line["type"] == "final_result"
    assert "diagnostic" not in line
