"""SdkAgentExecutor — behavior with a mocked claude_agent_sdk.query()."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from legion.agents.executor import SdkAgentExecutor
from legion.agents.invocation import AgentInvocation
from legion.safety.policy import default_policy_for_agent


def _inv(**kw) -> AgentInvocation:  # noqa: ANN003
    base = {
        "agent_type": "developer",
        "prompt": "say hello",
        "system_prompt": "",
        "system_context": "",
        "model": "claude-sonnet-4-6",
        "tools": ["Read"],
        "skills": [],
        "mcp_servers": {},
        "max_turns": 5,
        "timeout_seconds": 60,
        "execution_mode": "sdk",
        "auth_mode": "api_key",
        "safety_policy": default_policy_for_agent("developer"),
        "channel_config": None,
    }
    base.update(kw)
    return AgentInvocation(**base)


class _FakeBlock:
    def __init__(self, text: str = "") -> None:
        self.text = text


class _FakeAssistantMessage:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]
        self.model = "claude-sonnet-4-6"
        self.uuid = "u-1"


class _FakeResultMessage:
    def __init__(self, result: str, cost: float) -> None:
        self.result = result
        self.total_cost_usd = cost
        self.duration_ms = 1000
        self.num_turns = 1
        self.usage = {"input_tokens": 10, "output_tokens": 5}


async def _scripted_query(messages: list[Any]):
    for m in messages:
        yield m


@pytest.mark.asyncio
async def test_sdk_executor_returns_output(tmp_path, monkeypatch) -> None:
    # Point the executor at a tmp /workspace so settings.json writes don't
    # pollute the real FS.
    monkeypatch.setenv("LEGION_WORKSPACE_ROOT", str(tmp_path))

    messages = [
        _FakeAssistantMessage("hello"),
        _FakeResultMessage("hello", 0.012),
    ]

    captured_events: list[dict] = []

    def fake_writer(event: dict) -> None:
        captured_events.append(event)

    with patch("claude_agent_sdk.query", return_value=_scripted_query(messages)):
        executor = SdkAgentExecutor()
        out = await executor.run(
            _inv(),
            config={"configurable": {}, "_test_writer": fake_writer},
        )

    assert out.is_error is False
    assert "hello" in out.output
    assert out.cost_usd == pytest.approx(0.012)
    # Writer got TEXT + AGENT_COMPLETED / COST_UPDATE events.
    types = [e["type"] for e in captured_events]
    assert "text" in types
    assert "agent_completed" in types


@pytest.mark.asyncio
async def test_sdk_executor_writes_settings_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LEGION_WORKSPACE_ROOT", str(tmp_path))

    with patch(
        "claude_agent_sdk.query",
        return_value=_scripted_query([_FakeResultMessage("done", 0.001)]),
    ):
        executor = SdkAgentExecutor()
        await executor.run(
            _inv(),
            config={"configurable": {}, "_test_writer": lambda e: None},
        )

    settings_path = tmp_path / ".claude" / "settings.json"
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert "permissions" in data
    assert any("/workspace" in r for r in data["permissions"]["allow"])


@pytest.mark.asyncio
async def test_sdk_executor_timeout_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LEGION_WORKSPACE_ROOT", str(tmp_path))

    async def never_yields():
        import asyncio as _a

        await _a.sleep(10)
        yield _FakeResultMessage("x", 0.0)

    with patch("claude_agent_sdk.query", return_value=never_yields()):
        executor = SdkAgentExecutor()
        out = await executor.run(
            _inv(timeout_seconds=0),  # immediate timeout
            config={"configurable": {}, "_test_writer": lambda e: None},
        )

    assert out.is_error is True
    assert "timed out" in out.error_message.lower()
