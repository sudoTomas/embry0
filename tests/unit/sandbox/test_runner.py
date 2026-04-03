import json
from unittest.mock import patch

import pytest

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


def _make_fake_sdk(fake_query):
    """Return a fake claude_agent_sdk module object for use in patch.dict."""

    class FakeOptions:
        def __init__(self, **kw):
            pass

    class FakeModule:
        AssistantMessage = type("AM", (), {})
        ResultMessage = type("RM", (), {})
        TextBlock = type("TB", (), {})
        ToolUseBlock = type("TUB", (), {})
        ClaudeAgentOptions = FakeOptions
        query = staticmethod(fake_query)

    return FakeModule()


@pytest.mark.asyncio
async def test_system_context_prepended_to_prompt():
    """run_agent prepends system_context to the prompt before passing to SDK (I2)."""
    from legion.sandbox.runner import run_agent

    captured_prompt: list[str] = []

    async def fake_query(prompt: str, options: object):
        captured_prompt.append(prompt)
        # Yield nothing — just return immediately as an empty async generator.
        return
        yield  # pragma: no cover

    with patch.dict("sys.modules", {"claude_agent_sdk": _make_fake_sdk(fake_query)}):
        config = {
            "agent_type": "developer",
            "prompt": "do the task",
            "system_context": "You are a senior engineer.",
            "timeout_seconds": 5,
        }
        result = await run_agent(config)

    assert len(captured_prompt) == 1
    assert captured_prompt[0].startswith("You are a senior engineer.")
    assert "do the task" in captured_prompt[0]
    assert not result["is_error"]


@pytest.mark.asyncio
async def test_no_system_context_leaves_prompt_unchanged():
    """run_agent does not modify prompt when system_context is absent."""
    from legion.sandbox.runner import run_agent

    captured_prompt: list[str] = []

    async def fake_query(prompt: str, options: object):
        captured_prompt.append(prompt)
        return
        yield  # pragma: no cover

    with patch.dict("sys.modules", {"claude_agent_sdk": _make_fake_sdk(fake_query)}):
        config = {
            "agent_type": "developer",
            "prompt": "just the task",
            "timeout_seconds": 5,
        }
        await run_agent(config)

    assert len(captured_prompt) == 1
    assert captured_prompt[0] == "just the task"
