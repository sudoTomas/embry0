"""EMB-44: agent_ask_user events embedded in Bash tool results reach the
event stream, and the developer/review prompts document the mechanism."""

import json
from unittest.mock import patch

import pytest

from embry0.agents.executor import (
    _MAX_EMBEDDED_ASK_EVENTS,
    SdkAgentExecutor,
    _extract_embedded_ask_user_events,
)
from embry0.safety.policy import default_policy_for_agent


def _helper_output(question="Which DB should I use?", **kw):
    ev = {
        "type": "agent_ask_user",
        "timestamp": "t",
        "question": question,
        "category": "design",
        "options": [],
        "importance": "blocking",
    }
    ev.update(kw)
    return json.dumps(ev)


def test_extract_plain_json_line():
    events = _extract_embedded_ask_user_events("some output\n" + _helper_output() + "\ntrailing")
    assert len(events) == 1
    assert events[0]["question"] == "Which DB should I use?"
    assert events[0]["importance"] == "blocking"


def test_extract_inside_repr_of_block_list():
    inner = _helper_output(question="Repr survives?")
    wrapped = str([{"type": "text", "text": inner}])
    events = _extract_embedded_ask_user_events(wrapped)
    assert len(events) == 1
    assert events[0]["question"] == "Repr survives?"


def test_extract_multiple_and_cap():
    text = "\n".join(_helper_output(question=f"q{i}") for i in range(_MAX_EMBEDDED_ASK_EVENTS + 3))
    events = _extract_embedded_ask_user_events(text)
    assert len(events) == _MAX_EMBEDDED_ASK_EVENTS


def test_extract_ignores_questionless_and_malformed():
    text = '{"type": "agent_ask_user", "question": ""}\n{"type": "agent_ask_user", broken'
    assert _extract_embedded_ask_user_events(text) == []


def test_extract_no_marker_fast_path():
    assert _extract_embedded_ask_user_events("nothing to see here") == []


# ---------------------------------------------------------------------------
# Executor re-emission
# ---------------------------------------------------------------------------


class _TextBlock:
    def __init__(self, text):
        self.text = text


class _ToolResultBlock:
    def __init__(self, content):
        self.tool_use_id = "tu-1"
        self.content = content
        self.is_error = False


class _AssistantMessage:
    def __init__(self, content):
        self.content = content
        self.model = "claude-sonnet-4-6"
        self.uuid = "u-1"


class _ResultMessage:
    def __init__(self):
        self.result = "done"
        self.total_cost_usd = 0.001
        self.duration_ms = 10
        self.num_turns = 1
        self.usage = {"input_tokens": 1, "output_tokens": 1}


async def _scripted(messages):
    for m in messages:
        yield m


def _inv():
    from embry0.agents.invocation import AgentInvocation

    return AgentInvocation(
        agent_type="developer",
        prompt="go",
        system_prompt="",
        system_context="",
        model="claude-sonnet-4-6",
        tools=["Read", "Bash"],
        skills=[],
        mcp_servers={},
        max_turns=5,
        timeout_seconds=60,
        execution_mode="sdk",
        auth_mode="api_key",
        safety_policy=default_policy_for_agent("developer"),
        channel_config=None,
    )


@pytest.mark.asyncio
async def test_executor_reemits_embedded_ask_user_event(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    captured = []

    tool_result = _ToolResultBlock("cmd output\n" + _helper_output(question="Ship it?"))
    messages = [
        _AssistantMessage([tool_result]),
        _ResultMessage(),
    ]

    # Patch ToolResultBlock so isinstance matches our fake.
    with (
        patch("claude_agent_sdk.query", return_value=_scripted(messages)),
        patch("claude_agent_sdk.ToolResultBlock", _ToolResultBlock),
    ):
        out = await SdkAgentExecutor().run(
            _inv(),
            config={"configurable": {}, "_test_writer": captured.append},
        )

    assert out.is_error is False
    asks = [e for e in captured if e.get("type") == "agent_ask_user"]
    assert len(asks) == 1
    assert asks[0]["question"] == "Ship it?"
    assert asks[0]["node"] == "developer"
    # The regular tool_result event still flows.
    assert any(e.get("type") == "tool_result" for e in captured)


# ---------------------------------------------------------------------------
# Prompt contracts document the mechanism
# ---------------------------------------------------------------------------


def test_developer_prompts_document_ask_user():
    from embry0.workflows.issue_to_pr.nodes import (
        _build_developer_delta_prompt,
        _build_developer_full_prompt,
    )

    state = {"repo": "o/r", "task": "t", "issue_id": "iss"}
    for prompt in (
        _build_developer_full_prompt(state, "embry0/x"),
        _build_developer_delta_prompt(state, "embry0/x"),
    ):
        assert "from embry0.sandbox.ask_user import ask_user" in prompt
        assert "never reach the user" in prompt
