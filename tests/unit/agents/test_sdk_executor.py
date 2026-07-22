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
    assert completed["result"]["input_tokens"] == 1200
    assert completed["result"]["output_tokens"] == 340
    assert completed["result"]["cache_read_tokens"] == 98000
    assert completed["result"]["cache_creation_tokens"] == 4500


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


@pytest.mark.asyncio
async def test_sdk_executor_with_tool_result_block(tmp_path, monkeypatch) -> None:
    """Use real ToolResultBlock to hit the isinstance() branch."""
    from claude_agent_sdk import AssistantMessage, ResultMessage, ToolResultBlock

    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    events: list[dict] = []

    tool_result = ToolResultBlock(tool_use_id="t1", content="file content here", is_error=False)
    assistant_msg = AssistantMessage(content=[tool_result], model="claude-sonnet-4-6")
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
    tr_events = [e for e in events if e.get("type") == "tool_result"]
    assert len(tr_events) == 1
    assert tr_events[0]["tool_use_id"] == "t1"
    assert tr_events[0]["content"] == "file content here"


# ---------------------------------------------------------------------------
# hooks_unavailable warning path
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="sdk_hooks try/except removed in 2026-04-28-sandbox-hardening Task 3; hooks now fail-closed at boot"
)
@pytest.mark.asyncio
async def test_sdk_executor_hooks_unavailable_logs_warning(tmp_path, monkeypatch) -> None:
    """If options.hooks assignment raises, executor logs warning and continues."""
    from unittest.mock import MagicMock

    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))

    mock_options = MagicMock()
    # Make setting .hooks raise to simulate legacy SDK
    type(mock_options).hooks = property(
        fget=lambda self: None,
        fset=lambda self, v: (_ for _ in ()).throw(AttributeError("hooks not supported")),
    )

    with (
        patch(
            "claude_agent_sdk.query",
            return_value=_scripted_query([_FakeResultMessage("done", 0.001)]),
        ),
        patch("embry0.agents.executor.build_sdk_options", return_value=mock_options),
    ):
        executor = SdkAgentExecutor()
        out = await executor.run(
            _inv(),
            config={"configurable": {}, "_test_writer": lambda _e: None},
        )

    # Executor should not crash even when hooks assignment fails
    assert out.is_error is False


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
async def test_grok_overlay_prefers_xai_proxy_bearer(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("EMBRY0_XAI_PROXY_URL", "http://xai-proxy:9106")
    monkeypatch.setenv("EMBRY0_XAI_PROXY_TOKEN", "sandbox-bearer")
    monkeypatch.setenv("XAI_API_KEY", "console-key-should-be-ignored")

    out, options = await _run_capturing_options(_grok_inv())
    assert out.is_error is False
    assert options.env == {
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
