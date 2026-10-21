"""SdkAgentExecutor — behavior with a mocked claude_agent_sdk.query()."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from embry0.agents.executor import SdkAgentExecutor
from embry0.agents.invocation import AgentInvocation
from embry0.safety.policy import default_policy_for_agent


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
    def __init__(self, result: str, cost: float, usage: dict | None = "unset") -> None:  # type: ignore[assignment]
        self.result = result
        self.total_cost_usd = cost
        self.duration_ms = 1000
        self.num_turns = 1
        self.usage = {"input_tokens": 10, "output_tokens": 5} if usage == "unset" else usage


async def _scripted_query(messages: list[Any]):
    for m in messages:
        yield m


@pytest.mark.asyncio
async def test_sdk_executor_returns_output(tmp_path, monkeypatch) -> None:
    # Point the executor at a tmp /workspace so settings.json writes don't
    # pollute the real FS.
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))

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
async def test_sdk_executor_extracts_token_usage(tmp_path, monkeypatch) -> None:
    """EMB-35: all four usage token fields land on AgentOutput and the
    cost_update / agent_completed events."""
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))

    usage = {
        "input_tokens": 1200,
        "output_tokens": 340,
        "cache_read_input_tokens": 98000,
        "cache_creation_input_tokens": 4500,
    }
    captured_events: list[dict] = []

    with patch(
        "claude_agent_sdk.query",
        return_value=_scripted_query([_FakeResultMessage("done", 0.02, usage=usage)]),
    ):
        executor = SdkAgentExecutor()
        out = await executor.run(
            _inv(),
            config={"configurable": {}, "_test_writer": captured_events.append},
        )

    assert out.input_tokens == 1200
    assert out.output_tokens == 340
    assert out.cache_read_tokens == 98000
    assert out.cache_creation_tokens == 4500

    cost_update = next(e for e in captured_events if e["type"] == "cost_update")
    assert cost_update["tokens_in"] == 1200
    assert cost_update["tokens_out"] == 340
    assert cost_update["cache_read_tokens"] == 98000
    assert cost_update["cache_creation_tokens"] == 4500

    completed = next(e for e in captured_events if e["type"] == "agent_completed")
    assert completed["input_tokens"] == 1200
    assert completed["output_tokens"] == 340
    assert completed["cache_read_tokens"] == 98000
    assert completed["cache_creation_tokens"] == 4500


@pytest.mark.asyncio
async def test_sdk_executor_token_usage_none_defaults_zero(tmp_path, monkeypatch) -> None:
    """A ResultMessage with usage=None yields zero token counts, not an error."""
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))

    with patch(
        "claude_agent_sdk.query",
        return_value=_scripted_query([_FakeResultMessage("done", 0.001, usage=None)]),
    ):
        executor = SdkAgentExecutor()
        out = await executor.run(
            _inv(),
            config={"configurable": {}, "_test_writer": lambda e: None},
        )

    assert out.is_error is False
    assert out.input_tokens == 0
    assert out.output_tokens == 0
    assert out.cache_read_tokens == 0
    assert out.cache_creation_tokens == 0


@pytest.mark.asyncio
async def test_sdk_executor_writes_settings_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))

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
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))

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


class _FakeToolUseBlock:
    def __init__(self, name: str, tool_id: str = "t1") -> None:
        self.name = name
        self.id = tool_id
        self.input = {"command": "echo hi"}


class _FakeAssistantMessageWithTool:
    def __init__(self, tool_name: str) -> None:
        self.content = [_FakeToolUseBlock(tool_name)]
        self.model = "claude-sonnet-4-6"
        self.uuid = "u-t"


@pytest.mark.asyncio
async def test_sdk_executor_tools_called_counts_once_per_invocation(tmp_path, monkeypatch) -> None:
    """Regression: tools_called must not be double-counted (hook + block)."""
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))

    messages = [
        _FakeAssistantMessageWithTool("Bash"),
        _FakeAssistantMessageWithTool("Bash"),
        _FakeResultMessage("done", 0.001),
    ]

    with patch("claude_agent_sdk.query", return_value=_scripted_query(messages)):
        executor = SdkAgentExecutor()
        out = await executor.run(
            _inv(),
            config={"configurable": {}, "_test_writer": lambda e: None},
        )

    # Two Bash tool calls → count of 2 (not 4 from the old double-count bug).
    assert out.tools_called.get("Bash") == 2


# ---------------------------------------------------------------------------
# Helper-function unit tests
# ---------------------------------------------------------------------------


def test_workspace_root_respects_env_var(monkeypatch, tmp_path) -> None:
    from embry0.agents.executor import _workspace_root

    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    assert _workspace_root() == tmp_path


def test_workspace_root_default(monkeypatch) -> None:
    from embry0.agents.executor import _workspace_root

    monkeypatch.delenv("EMBRY0_WORKSPACE_ROOT", raising=False)
    assert str(_workspace_root()) == "/workspace"


def test_resolve_writer_returns_test_writer() -> None:
    from embry0.agents.executor import _resolve_writer

    calls = []

    def w(e):  # noqa: ANN001
        calls.append(e)

    writer = _resolve_writer({"_test_writer": w})
    assert writer is w


def test_resolve_writer_no_op_fallback() -> None:
    from embry0.agents.executor import _resolve_writer

    writer = _resolve_writer(None)
    # Should not raise and should be callable
    writer({"test": "event"})


def test_summarize_tool_input_read_uses_file_path() -> None:
    from embry0.agents.executor import _summarize_tool_input

    assert _summarize_tool_input("Read", {"file_path": "/a/b"}) == "/a/b"


def test_summarize_tool_input_glob_uses_pattern() -> None:
    from embry0.agents.executor import _summarize_tool_input

    assert _summarize_tool_input("Glob", {"pattern": "**/*.py"}) == "**/*.py"


def test_summarize_tool_input_write_uses_file_path() -> None:
    from embry0.agents.executor import _summarize_tool_input

    assert _summarize_tool_input("Write", {"file_path": "/out/f"}) == "/out/f"


def test_summarize_tool_input_edit_uses_file_path() -> None:
    from embry0.agents.executor import _summarize_tool_input

    assert _summarize_tool_input("Edit", {"file_path": "/out/f"}) == "/out/f"


def test_summarize_tool_input_bash_uses_command() -> None:
    from embry0.agents.executor import _summarize_tool_input

    assert _summarize_tool_input("Bash", {"command": "ls"}) == "ls"


def test_summarize_tool_input_non_dict() -> None:
    from embry0.agents.executor import _summarize_tool_input

    assert _summarize_tool_input("Read", "not a dict") == "not a dict"


def test_summarize_tool_input_unknown_tool() -> None:
    from embry0.agents.executor import _summarize_tool_input

    assert _summarize_tool_input("MysteryTool", {"key": "val"}) == "{'key': 'val'}"


# ---------------------------------------------------------------------------
# _evaluate_hook unit tests (Ring-3 hook extracted to module level)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_hook_allows_safe_command() -> None:
    """Allow returns the SDK's PreToolUseHookSpecificOutput shape with
    ``permissionDecision: "allow"`` (NOT the legacy top-level ``{"decision":
    "allow"}`` form, which the CLI Zod validator rejects)."""
    from embry0.agents.executor import _evaluate_hook
    from embry0.safety.policy import SafetyPolicy

    result = await _evaluate_hook(
        policy=SafetyPolicy(),
        tool_name="Bash",
        tool_input={"command": "echo hi"},
        tools_called={},
        writer=lambda _e: None,
    )
    # Top-level must NOT have decision="allow" — only "block" is valid there.
    assert result.get("decision") != "allow"
    spec = result["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "allow"


@pytest.mark.asyncio
async def test_evaluate_hook_denies_dangerous_command() -> None:
    """Deny returns ``hookSpecificOutput.permissionDecision: "deny"`` plus the
    blocked reason in ``permissionDecisionReason``."""
    from embry0.agents.executor import _evaluate_hook
    from embry0.safety.policy import ContentRule, SafetyPolicy

    deny_policy = SafetyPolicy(
        content_checks=[
            ContentRule(pattern=r"rm -rf /", tools=["Bash"], reason="destructive-rm"),
        ]
    )
    events: list[dict] = []
    result = await _evaluate_hook(
        policy=deny_policy,
        tool_name="Bash",
        tool_input={"command": "rm -rf /"},
        tools_called={},
        writer=lambda e: events.append(e),
    )
    spec = result["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "deny"
    assert "destructive-rm" in spec["permissionDecisionReason"]
    assert any(e.get("type") == "error" for e in events)


@pytest.mark.asyncio
async def test_evaluate_hook_non_dict_input_coerced() -> None:
    """Non-dict tool_input is coerced to {} (fail-safe) — should allow."""
    from embry0.agents.executor import _evaluate_hook
    from embry0.safety.policy import SafetyPolicy

    result = await _evaluate_hook(
        policy=SafetyPolicy(),
        tool_name="Bash",
        tool_input="not-a-dict",  # type: ignore[arg-type]
        tools_called={},
        writer=lambda _e: None,
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"


@pytest.mark.asyncio
async def test_evaluate_hook_does_not_set_top_level_decision_allow_or_deny() -> None:
    """Regression test for the 2026-05-06 CLI Zod-validation crash.

    The Claude CLI's stdin protocol validator rejects PreToolUse hook
    outputs whose top-level ``decision`` is anything other than
    ``"block"``. The legacy ``{"decision": "allow" | "deny"}`` shape that
    embry0 used to emit caused ``ZodError: Invalid input`` and crashed
    the runner subprocess (1 of 14 sub-tasks errored on the prior QA
    run because of this exact mismatch). The hook MUST express the
    decision via ``hookSpecificOutput.permissionDecision`` instead.
    """
    from embry0.agents.executor import _evaluate_hook
    from embry0.safety.policy import ContentRule, SafetyPolicy

    # Allow path
    allow = await _evaluate_hook(
        policy=SafetyPolicy(),
        tool_name="Read",
        tool_input={"file_path": "/workspace/foo"},
        tools_called={},
        writer=lambda _e: None,
    )
    assert allow.get("decision") not in ("allow", "deny")

    # Deny path
    deny_policy = SafetyPolicy(content_checks=[ContentRule(pattern=r"x", tools=["Bash"], reason="r")])
    deny = await _evaluate_hook(
        policy=deny_policy,
        tool_name="Bash",
        tool_input={"command": "x"},
        tools_called={},
        writer=lambda _e: None,
    )
    # If a top-level decision is present, it must only be "block" (the
    # legacy form) — never "allow" / "deny".
    assert deny.get("decision") in (None, "block")


# ---------------------------------------------------------------------------
# Exception path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sdk_executor_exception_sets_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))

    async def raises():
        raise RuntimeError("boom")
        yield  # make it an async generator  # noqa: F631

    with patch("claude_agent_sdk.query", return_value=raises()):
        executor = SdkAgentExecutor()
        out = await executor.run(
            _inv(),
            config={"configurable": {}, "_test_writer": lambda _e: None},
        )

    assert out.is_error is True
    assert "boom" in out.error_message


# ---------------------------------------------------------------------------
# Real SDK class isinstance branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sdk_executor_with_real_sdk_instances(tmp_path, monkeypatch) -> None:
    """Use actual SDK dataclass instances to hit the isinstance() fast-paths."""
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
    )

    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    events: list[dict] = []

    text_block = TextBlock(text="hello from sdk")
    assistant_msg = AssistantMessage(content=[text_block], model="claude-sonnet-4-6")
    result_msg = ResultMessage(
        subtype="result",
        duration_ms=100,
        duration_api_ms=80,
        is_error=False,
        num_turns=1,
        session_id="s1",
        total_cost_usd=0.005,
        usage={"input_tokens": 10, "output_tokens": 5},
        result="hello from sdk",
    )

    with patch(
        "claude_agent_sdk.query",
        return_value=_scripted_query([assistant_msg, result_msg]),
    ):
        executor = SdkAgentExecutor()
        out = await executor.run(
            _inv(),
            config={"configurable": {}, "_test_writer": lambda e: events.append(e)},
        )

    assert out.is_error is False
    assert "hello from sdk" in out.output
    types = [e["type"] for e in events]
    assert "text" in types


@pytest.mark.asyncio
async def test_sdk_executor_with_real_tool_use_block(tmp_path, monkeypatch) -> None:
    """Use real ToolUseBlock to hit the isinstance() fast-path."""
    from claude_agent_sdk import AssistantMessage, ResultMessage, ToolUseBlock

    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    events: list[dict] = []

    tool_block = ToolUseBlock(id="t1", name="Bash", input={"command": "echo hi"})
    assistant_msg = AssistantMessage(content=[tool_block], model="claude-sonnet-4-6")
    result_msg = ResultMessage(
        subtype="result",
        duration_ms=100,
        duration_api_ms=80,
        is_error=False,
        num_turns=1,
        session_id="s1",
        total_cost_usd=0.001,
        usage=None,
        result="done",
    )

    with patch(
        "claude_agent_sdk.query",
        return_value=_scripted_query([assistant_msg, result_msg]),
    ):
        executor = SdkAgentExecutor()
        out = await executor.run(
            _inv(),
            config={"configurable": {}, "_test_writer": lambda e: events.append(e)},
        )

    assert out.tools_called.get("Bash") == 1
    tool_events = [e for e in events if e.get("type") == "tool_call"]
    assert tool_events[0]["tool_name"] == "Bash"


@pytest.mark.asyncio
async def test_sdk_executor_with_thinking_block(tmp_path, monkeypatch) -> None:
    """Use real ThinkingBlock to hit the isinstance() branch."""
    from claude_agent_sdk import AssistantMessage, ResultMessage, ThinkingBlock

    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    events: list[dict] = []

    thinking_block = ThinkingBlock(thinking="I should check the file", signature="sig1")
    assistant_msg = AssistantMessage(content=[thinking_block], model="claude-sonnet-4-6")
    result_msg = ResultMessage(
        subtype="result",
        duration_ms=100,
        duration_api_ms=80,
        is_error=False,
        num_turns=1,
        session_id="s1",
        total_cost_usd=0.001,
        usage=None,
        result="done",
    )

    with patch(
        "claude_agent_sdk.query",
        return_value=_scripted_query([assistant_msg, result_msg]),
    ):
        executor = SdkAgentExecutor()
        out = await executor.run(
            _inv(),
            config={"configurable": {}, "_test_writer": lambda e: events.append(e)},
        )

    assert out.is_error is False
    thinking_events = [e for e in events if e.get("type") == "thinking"]
    assert len(thinking_events) == 1
    assert "I should check" in thinking_events[0]["text"]


class _FakeToolUse:
    def __init__(self, id_: str, name: str, input_: dict) -> None:
        self.id = id_
        self.name = name
        self.input = input_


class _FakeToolResultBlock:
    def __init__(self, tool_use_id: str, content, is_error: bool | None = False) -> None:  # noqa: ANN001
        self.tool_use_id = tool_use_id
        self.content = content
        self.is_error = is_error


class _FakeUserMessage:
    def __init__(self, content, parent_tool_use_id: str | None = None) -> None:  # noqa: ANN001
        self.content = content
        self.parent_tool_use_id = parent_tool_use_id


def _assistant_with(
    blocks: list, model: str = "claude-sonnet-4-6", parent: str | None = None, usage: dict | None = None
):  # noqa: ANN001
    msg = _FakeAssistantMessage("")
    msg.content = blocks
    msg.model = model
    msg.parent_tool_use_id = parent
    msg.usage = usage
    return msg


async def _run(messages: list, monkeypatch, tmp_path, **inv) -> list[dict]:  # noqa: ANN001
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    events: list[dict] = []
    with patch("claude_agent_sdk.query", return_value=_scripted_query(messages)):
        await SdkAgentExecutor().run(_inv(**inv), config={"configurable": {}, "_test_writer": events.append})
    return events


@pytest.mark.asyncio
async def test_tool_result_on_a_user_message_is_emitted_and_paired(tmp_path, monkeypatch) -> None:
    messages = [
        _assistant_with([_FakeToolUse("t1", "Read", {"file_path": "a.py"})]),
        _FakeUserMessage([_FakeToolResultBlock("t1", "file content here")]),
        _FakeResultMessage("done", 0.001),
    ]
    events = await _run(messages, monkeypatch, tmp_path, run_id="run-1", node="developer")
    results = [e for e in events if e["type"] == "tool_result"]
    assert len(results) == 1, "a ToolResultBlock on a UserMessage must become one tool_result event"
    assert results[0]["tool_id"] == "t1"
    assert results[0]["content"] == "file content here"
    assert results[0]["is_error"] is False
    assert results[0]["run_id"] == "run-1" and results[0]["node"] == "developer"


@pytest.mark.asyncio
async def test_tool_result_list_content_renders_text_and_marks_images(tmp_path, monkeypatch) -> None:
    content = [
        {"type": "text", "text": "first"},
        {"type": "image", "source": {"data": "AAAA"}},
        {"type": "text", "text": "last"},
    ]
    messages = [
        _assistant_with([_FakeToolUse("t1", "mcp__playwright__browser_snapshot", {})]),
        _FakeUserMessage([_FakeToolResultBlock("t1", content)]),
        _FakeResultMessage("done", 0.001),
    ]
    events = await _run(messages, monkeypatch, tmp_path)
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["content"] == "first\n[image]\nlast"
    assert "AAAA" not in result["content"], "image bytes must never ride the event"


@pytest.mark.asyncio
async def test_user_message_with_string_content_emits_no_tool_result(tmp_path, monkeypatch) -> None:
    messages = [_FakeUserMessage("the prompt echoed back"), _FakeResultMessage("done", 0.001)]
    events = await _run(messages, monkeypatch, tmp_path)
    assert not [e for e in events if e["type"] == "tool_result"]


@pytest.mark.asyncio
async def test_long_tool_result_is_cut_with_a_marker(tmp_path, monkeypatch) -> None:
    messages = [
        _assistant_with([_FakeToolUse("t1", "Bash", {"command": "cat big"})]),
        _FakeUserMessage([_FakeToolResultBlock("t1", "x" * 5000)]),
        _FakeResultMessage("done", 0.001),
    ]
    events = await _run(messages, monkeypatch, tmp_path)
    result = next(e for e in events if e["type"] == "tool_result")
    assert len(result["content"]) == 4000
    assert result["truncated"] is True and result["original_length"] == 5000


@pytest.mark.asyncio
async def test_every_executor_event_carries_run_id_node_and_agent(tmp_path, monkeypatch) -> None:
    messages = [
        _assistant_with([_FakeBlock("hi"), _FakeToolUse("t1", "Read", {"file_path": "a"})]),
        _FakeUserMessage([_FakeToolResultBlock("t1", "ok")]),
        _FakeResultMessage("done", 0.001),
    ]
    events = await _run(messages, monkeypatch, tmp_path, run_id="run-9", node="generic_agent", agent_type="research")
    for e in events:
        assert e.get("run_id") == "run-9", f"{e['type']} lacks run_id"
        assert e.get("node") == "generic_agent", f"{e['type']} lacks node"
        assert e.get("agent") == "research", f"{e['type']} lacks agent"


@pytest.mark.asyncio
async def test_stream_failure_error_event_uses_message_and_code(tmp_path, monkeypatch) -> None:
    async def _boom():
        raise RuntimeError("socket closed")
        yield  # pragma: no cover

    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    events: list[dict] = []
    with patch("claude_agent_sdk.query", return_value=_boom()):
        out = await SdkAgentExecutor().run(
            _inv(run_id="r", node="developer"), config={"configurable": {}, "_test_writer": events.append}
        )
    assert out.is_error
    err = next(e for e in events if e["type"] == "error")
    assert err["message"] == "socket closed"
    assert "error" not in err
    assert err["node"] == "developer" and err["run_id"] == "r"


@pytest.mark.asyncio
async def test_policy_denial_error_event_names_the_tool_and_the_code() -> None:
    from embry0.agents.executor import _evaluate_hook

    events: list[dict] = []
    policy = default_policy_for_agent("developer")
    await _evaluate_hook(
        policy,
        "Bash",
        {"command": "rm -rf /"},
        {},
        events.append,
        {},
        ids={"run_id": "r", "node": "developer", "agent": "developer"},
    )
    err = next(e for e in events if e["type"] == "error")
    assert err["tool_name"] == "Bash"
    assert err["code"] == "ERR_TOOL_DENIED"
    assert err["message"], "the denial reason is the message"
    assert err["node"] == "developer"


# ---------------------------------------------------------------------------
# writer emission edge branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sdk_executor_result_message_uses_fallback_result(tmp_path, monkeypatch) -> None:
    """When output_text is empty and result message has .result, use it."""
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    events: list[dict] = []

    # No AssistantMessage → output_text stays empty → result comes from ResultMessage.result
    with patch(
        "claude_agent_sdk.query",
        return_value=_scripted_query([_FakeResultMessage("fallback output", 0.002)]),
    ):
        executor = SdkAgentExecutor()
        out = await executor.run(
            _inv(),
            config={"configurable": {}, "_test_writer": lambda e: events.append(e)},
        )

    assert out.output == "fallback output"
    cost_events = [e for e in events if e.get("type") == "cost_update"]
    assert cost_events[0]["cost_usd"] == pytest.approx(0.002)


@pytest.mark.asyncio
async def test_sdk_executor_system_context_prepended(tmp_path, monkeypatch) -> None:
    """system_context is prepended to the prompt when non-empty."""
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    captured_prompts: list[str] = []

    async def capturing_query(prompt, options):  # noqa: ARG001
        captured_prompts.append(prompt)
        yield _FakeResultMessage("done", 0.0)

    with patch("claude_agent_sdk.query", side_effect=capturing_query):
        executor = SdkAgentExecutor()
        await executor.run(
            _inv(prompt="do work", system_context="Be careful."),
            config={"configurable": {}, "_test_writer": lambda _e: None},
        )

    assert captured_prompts[0].startswith("Be careful.")
    assert "do work" in captured_prompts[0]


# ---------------------------------------------------------------------------
# EMB-46: provider env overlay — SDK over the xai-proxy with the SuperGrok
# bearer by default; EMB-36 console-key fallback; fail-closed otherwise.
# ---------------------------------------------------------------------------


def _grok_inv(**kw):  # noqa: ANN003, ANN202
    kw.setdefault("model", "grok-4.5")
    kw.setdefault("provider", "xai")
    return _inv(**kw)


async def _run_capturing_options(inv):  # noqa: ANN001, ANN202
    captured: dict[str, Any] = {}

    def fake_query(*, prompt, options):  # noqa: ANN001, ANN202
        captured["options"] = options
        return _scripted_query([_FakeResultMessage("done", 0.0)])

    with patch("claude_agent_sdk.query", side_effect=fake_query):
        out = await SdkAgentExecutor().run(inv, config={"configurable": {}, "_test_writer": lambda e: None})
    return out, captured.get("options")


@pytest.mark.asyncio
async def test_anthropic_api_key_run_does_not_overlay_auth_env(tmp_path, monkeypatch) -> None:
    """An Anthropic api_key run keeps the container env. The SDK options must not blank the key or inject OAuth."""
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-container")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oat-container")

    out, options = await _run_capturing_options(_inv(auth_mode="api_key", provider=None))
    assert out.is_error is False
    assert options.env == {"ENABLE_TOOL_SEARCH": "false"}, (
        f"an anthropic api_key run must not overlay auth env, got {options.env!r}"
    )
    assert "ANTHROPIC_API_KEY" not in options.env
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in options.env


@pytest.mark.asyncio
async def test_grok_overlay_prefers_xai_proxy_bearer(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("EMBRY0_XAI_PROXY_URL", "http://xai-proxy:9106")
    monkeypatch.setenv("EMBRY0_XAI_PROXY_TOKEN", "sandbox-bearer")
    monkeypatch.setenv("XAI_API_KEY", "console-key-should-be-ignored")

    out, options = await _run_capturing_options(_grok_inv())
    assert out.is_error is False
    assert options.env == {
        # #240: the base tool-search kill-switch survives the provider merge.
        "ENABLE_TOOL_SEARCH": "false",
        "ANTHROPIC_BASE_URL": "http://xai-proxy:9106",
        "ANTHROPIC_AUTH_TOKEN": "sandbox-bearer",
        "ANTHROPIC_API_KEY": "",
        "CLAUDE_CODE_OAUTH_TOKEN": "",
    }


@pytest.mark.asyncio
async def test_grok_overlay_console_key_fallback_when_proxy_down(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("EMBRY0_XAI_PROXY_URL", raising=False)
    monkeypatch.delenv("EMBRY0_XAI_PROXY_TOKEN", raising=False)
    monkeypatch.setenv("XAI_API_KEY", "console-key")

    out, options = await _run_capturing_options(_grok_inv())
    assert out.is_error is False
    assert options.env["ANTHROPIC_BASE_URL"] == "https://api.x.ai"
    assert options.env["ANTHROPIC_API_KEY"] == "console-key"
    assert options.env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert options.env["CLAUDE_CODE_OAUTH_TOKEN"] == ""


@pytest.mark.asyncio
async def test_grok_overlay_fails_closed_without_any_credential(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("EMBRY0_XAI_PROXY_URL", raising=False)
    monkeypatch.delenv("EMBRY0_XAI_PROXY_TOKEN", raising=False)
    monkeypatch.setenv("EMBRY0_XAI_PROXY_TOKEN_PATH", str(tmp_path / "absent"))
    monkeypatch.delenv("XAI_API_KEY", raising=False)

    out, options = await _run_capturing_options(_grok_inv())
    assert out.is_error is True
    assert "XAI_API_KEY" in out.error_message and "xai-proxy" in out.error_message
    assert options is None  # failed closed before any SDK call


@pytest.mark.asyncio
async def test_grok_overlay_ignores_stale_bearer_without_proxy_url(tmp_path, monkeypatch) -> None:
    """A bearer without a proxy URL must not hijack the base_url — fall back to console key."""
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("EMBRY0_XAI_PROXY_URL", raising=False)
    monkeypatch.setenv("EMBRY0_XAI_PROXY_TOKEN", "stale-bearer")
    monkeypatch.setenv("XAI_API_KEY", "console-key")

    out, options = await _run_capturing_options(_grok_inv())
    assert out.is_error is False
    assert options.env["ANTHROPIC_BASE_URL"] == "https://api.x.ai"
    assert options.env["ANTHROPIC_API_KEY"] == "console-key"


@pytest.mark.asyncio
async def test_evaluate_hook_counts_denials_by_reason() -> None:
    """Denials accumulate, keyed by reason, so callers can read them off state.

    Emitting the denial as an event is not enough: events are not durable state,
    so the pause summary an operator reads had no way to say that an agent spent
    its turn budget on a tool the policy would never allow.
    """
    from embry0.agents.executor import _evaluate_hook
    from embry0.safety.policy import ContentRule, SafetyPolicy

    policy = SafetyPolicy(content_checks=[ContentRule(pattern=r"\bdocker\b", tools=["Bash"], reason="no docker")])
    denials: dict[str, int] = {}

    for _ in range(3):
        await _evaluate_hook(
            policy=policy,
            tool_name="Bash",
            tool_input={"command": "docker ps"},
            tools_called={},
            writer=lambda _e: None,
            policy_denials=denials,
        )

    assert sum(denials.values()) == 3
    assert any("no docker" in reason for reason in denials)


@pytest.mark.asyncio
async def test_evaluate_hook_leaves_counter_untouched_when_allowed() -> None:
    """An allowed call must not appear in the denial counter."""
    from embry0.agents.executor import _evaluate_hook
    from embry0.safety.policy import SafetyPolicy

    denials: dict[str, int] = {}
    await _evaluate_hook(
        policy=SafetyPolicy(),
        tool_name="Bash",
        tool_input={"command": "echo hi"},
        tools_called={},
        writer=lambda _e: None,
        policy_denials=denials,
    )
    assert denials == {}


@pytest.mark.asyncio
async def test_evaluate_hook_without_a_counter_still_denies() -> None:
    """The counter is optional — omitting it must not change the verdict."""
    from embry0.agents.executor import _evaluate_hook
    from embry0.safety.policy import ContentRule, SafetyPolicy

    policy = SafetyPolicy(content_checks=[ContentRule(pattern=r"\bdocker\b", tools=["Bash"], reason="no docker")])
    result = await _evaluate_hook(
        policy=policy,
        tool_name="Bash",
        tool_input={"command": "docker ps"},
        tools_called={},
        writer=lambda _e: None,
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
async def test_denial_tells_the_agent_the_block_is_permanent() -> None:
    """First denial must say retrying cannot work.

    The bare reason reads like a transient error, so the agent re-tries with
    different quoting or a wrapper until the turn cap kills the run.
    """
    from embry0.agents.executor import _evaluate_hook
    from embry0.safety.policy import ContentRule, SafetyPolicy

    policy = SafetyPolicy(content_checks=[ContentRule(pattern=r"\bdocker\b", tools=["Bash"], reason="no docker")])
    result = await _evaluate_hook(
        policy=policy,
        tool_name="Bash",
        tool_input={"command": "docker ps"},
        tools_called={},
        writer=lambda _e: None,
        policy_denials={},
    )
    spec = result["hookSpecificOutput"]
    assert spec["permissionDecision"] == "deny"
    text = spec["permissionDecisionReason"]
    assert "no docker" in text, "the original reason must survive"
    assert "not a transient failure" in text
    assert "denied again" in text


@pytest.mark.asyncio
async def test_repeat_denial_escalates_and_names_the_count() -> None:
    """By the third attempt the agent should be told to stop and report."""
    from embry0.agents.executor import _evaluate_hook
    from embry0.safety.policy import ContentRule, SafetyPolicy

    policy = SafetyPolicy(content_checks=[ContentRule(pattern=r"\bdocker\b", tools=["Bash"], reason="no docker")])
    denials: dict[str, int] = {}
    text = ""
    for _ in range(3):
        result = await _evaluate_hook(
            policy=policy,
            tool_name="Bash",
            tool_input={"command": "docker ps"},
            tools_called={},
            writer=lambda _e: None,
            policy_denials=denials,
        )
        text = result["hookSpecificOutput"]["permissionDecisionReason"]

    assert "3 times" in text
    assert "Stop retrying" in text
    assert "preserve your work" in text, "the agent should be told to salvage the run, not just give up"


@pytest.mark.asyncio
async def test_guidance_does_not_fragment_the_denial_counter() -> None:
    """The counter keys on the RAW reason, not the enriched text.

    Guarding this explicitly: keying on the guidance would create a fresh
    counter entry per attempt (the text embeds the attempt number), silently
    breaking both the escalation and the pause summary that reads these counts.
    """
    from embry0.agents.executor import _evaluate_hook
    from embry0.safety.policy import ContentRule, SafetyPolicy

    policy = SafetyPolicy(content_checks=[ContentRule(pattern=r"\bdocker\b", tools=["Bash"], reason="no docker")])
    denials: dict[str, int] = {}
    for _ in range(4):
        await _evaluate_hook(
            policy=policy,
            tool_name="Bash",
            tool_input={"command": "docker ps"},
            tools_called={},
            writer=lambda _e: None,
            policy_denials=denials,
        )

    assert len(denials) == 1, f"counter fragmented into {len(denials)} keys"
    assert sum(denials.values()) == 4
    assert all("transient" not in k for k in denials), "guidance text leaked into the counter key"


@pytest.mark.asyncio
async def test_denial_guidance_without_a_counter_still_reads_as_permanent() -> None:
    """No counter passed → attempts unknown → still say it is permanent."""
    from embry0.agents.executor import _evaluate_hook
    from embry0.safety.policy import ContentRule, SafetyPolicy

    policy = SafetyPolicy(content_checks=[ContentRule(pattern=r"\bdocker\b", tools=["Bash"], reason="no docker")])
    result = await _evaluate_hook(
        policy=policy,
        tool_name="Bash",
        tool_input={"command": "docker ps"},
        tools_called={},
        writer=lambda _e: None,
    )
    assert "not a transient failure" in result["hookSpecificOutput"]["permissionDecisionReason"]


class _FakeMultiBlockMessage:
    def __init__(self, *texts: str) -> None:
        self.content = [_FakeBlock(t) for t in texts]
        self.model = "claude-sonnet-4-6"
        self.uuid = "u-2"


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["TITLE: Fix the mailbox quota", "NO_ISSUE", '{"decision": "approve"}'])
async def test_sdk_executor_second_message_starts_on_its_own_line(tmp_path, monkeypatch, token: str) -> None:
    """#329: a line-anchored token opening a new assistant message must not be
    glued to the last word of the previous message's narration."""
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    messages = [
        _FakeAssistantMessage("I checked the existing tickets."),
        _FakeAssistantMessage(token),
        _FakeResultMessage("ignored", 0.01),
    ]
    with patch("claude_agent_sdk.query", return_value=_scripted_query(messages)):
        out = await SdkAgentExecutor().run(_inv(), config={"configurable": {}, "_test_writer": lambda e: None})

    assert out.output == f"I checked the existing tickets.\n\n{token}"
    assert token in out.output.splitlines()


@pytest.mark.asyncio
async def test_sdk_executor_blocks_of_one_message_are_not_split(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    messages = [_FakeMultiBlockMessage("The rate is ", "12 per minute."), _FakeResultMessage("x", 0.01)]
    with patch("claude_agent_sdk.query", return_value=_scripted_query(messages)):
        out = await SdkAgentExecutor().run(_inv(), config={"configurable": {}, "_test_writer": lambda e: None})
    assert out.output == "The rate is 12 per minute."


@pytest.mark.asyncio
async def test_sdk_executor_tail_is_cut_after_joining_on_a_line_boundary(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    messages = [
        _FakeAssistantMessage("narration line\n" * 2000),
        _FakeAssistantMessage("NO_ISSUE"),
        _FakeResultMessage("x", 0.01),
    ]
    with patch("claude_agent_sdk.query", return_value=_scripted_query(messages)):
        out = await SdkAgentExecutor().run(_inv(), config={"configurable": {}, "_test_writer": lambda e: None})
    assert len(out.output) <= 10000
    assert out.output.startswith("narration line\n")
    assert out.output.splitlines()[-1] == "NO_ISSUE"


@pytest.mark.asyncio
async def test_sdk_executor_result_fallback_still_used_when_no_text(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    with patch("claude_agent_sdk.query", return_value=_scripted_query([_FakeResultMessage("only result", 0.01)])):
        out = await SdkAgentExecutor().run(_inv(), config={"configurable": {}, "_test_writer": lambda e: None})
    assert out.output == "only result"


@pytest.mark.asyncio
async def test_sdk_executor_output_parses_as_a_watcher_proposal_without_glue(tmp_path, monkeypatch) -> None:
    """Executor output -> parse_verdict, with the glue tolerance REMOVED: new
    rows no longer need it (it stays in the watcher for rows stored before #329)."""
    import re

    from embry0.services import watcher

    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    # Narration ending in a word, not sentence punctuation: the case the
    # watcher's _GLUE tolerance could never rescue.
    messages = [
        _FakeAssistantMessage("Now I will write up what I found in the logs"),
        _FakeAssistantMessage(
            "TITLE: Mailbox sync hits 403 quota\nSEVERITY: high\nFINGERPRINT: mailbox-403\nBODY:\nDetails."
        ),
        _FakeResultMessage("x", 0.01),
    ]
    with patch("claude_agent_sdk.query", return_value=_scripted_query(messages)):
        out = await SdkAgentExecutor().run(_inv(), config={"configurable": {}, "_test_writer": lambda e: None})

    monkeypatch.setattr(watcher, "_TITLE_RE", re.compile(r"^TITLE:[ \t]*(.+)$", re.MULTILINE))
    verdict = watcher.parse_verdict(out.output)
    assert verdict["kind"] == "proposal"
    assert verdict["proposal"]["title"] == "Mailbox sync hits 403 quota"
    assert verdict["proposal"]["severity"] == "high"


@pytest.mark.asyncio
async def test_turn_usage_is_emitted_per_assistant_message_with_a_priced_delta(tmp_path, monkeypatch) -> None:
    usage = {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    messages = [
        _assistant_with([_FakeBlock("one")], model="claude-sonnet-5", usage=usage),
        _assistant_with([_FakeBlock("two")], model="claude-sonnet-5", usage=usage),
        _FakeResultMessage("done", 0.009),
    ]
    events = await _run(messages, monkeypatch, tmp_path)
    turn_usage = [e for e in events if e["type"] == "turn_usage"]
    assert len(turn_usage) == 2, "one turn_usage per assistant message carrying usage"
    assert turn_usage[0]["input_tokens"] == 1000 and turn_usage[0]["output_tokens"] == 100
    assert turn_usage[0]["cost_usd"] == pytest.approx(0.003 + 0.0015)
    assert turn_usage[0]["model"] == "claude-sonnet-5"
    assert len([e for e in events if e["type"] == "cost_update"]) == 1, "cost_update stays once per run"


@pytest.mark.asyncio
async def test_assistant_message_without_usage_emits_no_turn_usage(tmp_path, monkeypatch) -> None:
    messages = [_assistant_with([_FakeBlock("one")], usage=None), _FakeResultMessage("done", 0.001)]
    events = await _run(messages, monkeypatch, tmp_path)
    assert not [e for e in events if e["type"] == "turn_usage"]
    assert [e for e in events if e["type"] == "turn_start"][0]["turn_number"] == 1


@pytest.mark.asyncio
async def test_unpriced_model_turn_usage_has_null_cost(tmp_path, monkeypatch) -> None:
    usage = {"input_tokens": 10, "output_tokens": 10}
    messages = [
        _assistant_with([_FakeBlock("one")], model="claude-experimental-9", usage=usage),
        _FakeResultMessage("done", 0.0),
    ]
    events = await _run(messages, monkeypatch, tmp_path)
    assert next(e for e in events if e["type"] == "turn_usage")["cost_usd"] is None


@pytest.mark.asyncio
async def test_subagent_messages_are_bracketed_and_do_not_count_as_turns(tmp_path, monkeypatch) -> None:
    messages = [
        _assistant_with([_FakeToolUse("task-1", "Task", {"subagent_type": "Explore", "prompt": "look"})]),
        _assistant_with([_FakeBlock("sub says hi")], parent="task-1"),
        _assistant_with([_FakeToolUse("t2", "Read", {"file_path": "x"})], parent="task-1"),
        _FakeUserMessage([_FakeToolResultBlock("t2", "contents")], parent_tool_use_id="task-1"),
        _FakeUserMessage([_FakeToolResultBlock("task-1", "sub result")]),
        _assistant_with([_FakeBlock("parent continues")]),
        _FakeResultMessage("done", 0.001),
    ]
    events = await _run(messages, monkeypatch, tmp_path)
    types = [e["type"] for e in events]
    start = next(e for e in events if e["type"] == "subagent_start")
    assert start["tool_id"] == "task-1" and start["name"] == "Explore"
    assert types.index("subagent_start") < types.index("subagent_end")
    end = next(e for e in events if e["type"] == "subagent_end")
    assert end["tool_id"] == "task-1" and end["is_error"] is False
    sub_text = next(e for e in events if e["type"] == "text" and e["text"] == "sub says hi")
    assert sub_text["parent_tool_use_id"] == "task-1"
    turns = [e["turn_number"] for e in events if e["type"] == "turn_start" and "parent_tool_use_id" not in e]
    assert turns == [1, 2], "only top-level assistant messages count as turns"
    assert (
        next(e for e in events if e["type"] == "tool_result" and e["tool_id"] == "t2")["parent_tool_use_id"] == "task-1"
    )


@pytest.mark.asyncio
async def test_tool_result_for_an_unknown_parent_emits_no_subagent_start(tmp_path, monkeypatch) -> None:
    messages = [
        _FakeUserMessage([_FakeToolResultBlock("t-orphan", "late")], parent_tool_use_id="never-seen"),
        _FakeResultMessage("done", 0.001),
    ]
    events = await _run(messages, monkeypatch, tmp_path)
    assert [e for e in events if e["type"] == "tool_result"][0]["tool_id"] == "t-orphan"
    assert not [e for e in events if e["type"] == "subagent_start"]


@pytest.mark.asyncio
async def test_compact_boundary_system_message_emits_context_compacted(tmp_path, monkeypatch) -> None:
    class _FakeSystemMessage:
        def __init__(self) -> None:
            self.subtype = "compact_boundary"
            self.data = {"compact_metadata": {"pre_tokens": 123456}}

    messages = [_FakeSystemMessage(), _assistant_with([_FakeBlock("after")]), _FakeResultMessage("done", 0.001)]
    events = await _run(messages, monkeypatch, tmp_path)
    compacted = next(e for e in events if e["type"] == "context_compacted")
    assert compacted["pre_tokens"] == 123456


@pytest.mark.asyncio
async def test_ask_user_recovered_from_a_subagent_tool_result_carries_the_parent(tmp_path, monkeypatch) -> None:
    """Spec 3.3: every event under a parent carries parent_tool_use_id, the
    agent_ask_user recovered from a subagent's Bash output included."""
    ask = json.dumps({"type": "agent_ask_user", "question": "which db?", "category": "general", "options": []})
    messages = [
        _assistant_with([_FakeToolUse("task-1", "Task", {"subagent_type": "Explore"})]),
        _assistant_with([_FakeToolUse("b1", "Bash", {"command": "ask"})], parent="task-1"),
        _FakeUserMessage([_FakeToolResultBlock("b1", ask)], parent_tool_use_id="task-1"),
        _FakeUserMessage([_FakeToolResultBlock("task-1", "sub result")]),
        _FakeResultMessage("done", 0.001),
    ]
    events = await _run(messages, monkeypatch, tmp_path)
    asked = next(e for e in events if e["type"] == "agent_ask_user")
    assert asked["parent_tool_use_id"] == "task-1", f"ask_user from a subagent lost its parent: {asked}"


@pytest.mark.asyncio
async def test_executor_scrubs_text_and_tool_results_with_the_sandbox_env_secrets(tmp_path, monkeypatch) -> None:
    secret = "env-secret-value-9876543210"
    monkeypatch.setenv("SOME_SERVICE_TOKEN", secret)
    messages = [
        _assistant_with([_FakeBlock(f"the token is {secret}"), _FakeToolUse("t1", "Bash", {"command": "cat .env"})]),
        _FakeUserMessage([_FakeToolResultBlock("t1", f"SOME_SERVICE_TOKEN={secret}\n")]),
        _FakeResultMessage(f"done, used {secret}", 0.001),
    ]
    events_out = await _run(messages, monkeypatch, tmp_path)
    text = next(e for e in events_out if e["type"] == "text")
    result = next(e for e in events_out if e["type"] == "tool_result")
    completed = next(e for e in events_out if e["type"] == "agent_completed")
    assert secret not in text["text"] and "***REDACTED***" in text["text"]
    assert secret not in result["content"]
    assert secret not in completed["output"]


@pytest.mark.asyncio
async def test_a_secret_straddling_the_cap_leaves_no_fragment(tmp_path, monkeypatch) -> None:
    """Redact before cap (spec 3.6): a token cut in half matches no pattern."""
    secret = "straddle-secret-abcdefghij0123456789"
    monkeypatch.setenv("STRADDLE_TOKEN", secret)
    # The cut at 4000 lands inside the 36-char secret (3980 + 36 > 4000); the
    # 14-char marker that replaces it fits whole (3980 + 14 < 4000).
    content = "p" * 3980 + secret + "q" * 200
    messages = [
        _assistant_with([_FakeToolUse("t1", "Bash", {"command": "x"})]),
        _FakeUserMessage([_FakeToolResultBlock("t1", content)]),
        _FakeResultMessage("done", 0.001),
    ]
    events_out = await _run(messages, monkeypatch, tmp_path)
    result = next(e for e in events_out if e["type"] == "tool_result")
    assert secret[:10] not in result["content"], "a fragment of the secret survived the cut"
    assert "***REDACTED***" in result["content"]
