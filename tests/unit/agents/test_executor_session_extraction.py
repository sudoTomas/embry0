"""SdkAgentExecutor — Plan C closeout: extracts conversation state.

Verifies that ``SdkAgentExecutor.run()`` populates the post-run session
fields on the returned ``AgentOutput`` so AgentRunner / run_agent_node /
the workflow nodes can persist them via ``AgentSessionsRepository``:

- api_key (anthropic_api) mode → ``messages`` carries the full
  [{role, content}, ...] history. The user prompt is the first turn,
  followed by one assistant turn per ``AssistantMessage`` (text blocks
  collapsed; tool_use / thinking blocks dropped — the Messages-API
  resume only consumes textual content).
- oauth (claude_max) mode → ``session_id`` is captured from any
  message that carries one (AssistantMessage.session_id or
  ResultMessage.session_id), and ``session_blob_path`` is the canonical
  in-sandbox JSONL path the AgentRunner will ``docker cp`` from.

Also verifies the negative cases: error/timeout paths must NOT emit a
half-formed session (there's no useful state to resume from), and the
``api_key`` path does NOT populate session_id/session_blob_path.

Tests mock ``claude_agent_sdk.query()`` with simple namespaces — no
real SDK install needed beyond the import surface.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from athanor.agents.executor import SdkAgentExecutor
from athanor.agents.invocation import AgentInvocation
from athanor.safety.policy import default_policy_for_agent


def _inv(**kw: Any) -> AgentInvocation:
    base: dict[str, Any] = {
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


class _TextBlock:
    """Duck-typed TextBlock — has ``text``, no ``name``/``input``."""

    def __init__(self, text: str) -> None:
        self.text = text


class _ToolUseBlock:
    """Duck-typed ToolUseBlock — has ``name``/``id``/``input``, no ``text``."""

    def __init__(self, name: str, id_: str = "tu-1", input_: dict[str, Any] | None = None) -> None:
        self.name = name
        self.id = id_
        self.input = input_ or {}


class _AssistantMessage:
    """Duck-typed AssistantMessage — has ``content`` + ``model``, no ``total_cost_usd``."""

    def __init__(
        self,
        content: list[Any],
        *,
        model: str = "claude-sonnet-4-6",
        session_id: str | None = None,
    ) -> None:
        self.content = content
        self.model = model
        # SDK's AssistantMessage.session_id (types.py); the executor
        # snapshots this into AgentOutput.session_id for oauth/claude_max.
        self.session_id = session_id
        self.uuid = "u-1"


class _ResultMessage:
    """Duck-typed ResultMessage — has ``total_cost_usd`` + ``num_turns``."""

    def __init__(
        self,
        result: str = "",
        *,
        cost: float = 0.0,
        session_id: str | None = None,
    ) -> None:
        self.result = result
        self.total_cost_usd = cost
        self.duration_ms = 1000
        self.num_turns = 1
        self.usage = {"input_tokens": 1, "output_tokens": 1}
        self.session_id = session_id


async def _scripted_query(messages: list[Any]):
    for m in messages:
        yield m


@pytest.mark.asyncio
async def test_api_key_mode_captures_full_message_history(tmp_path: Any, monkeypatch: Any) -> None:
    """api_key mode: AgentOutput.messages == [user_prompt, assistant_turn_1, ...]."""
    monkeypatch.setenv("ATHANOR_WORKSPACE_ROOT", str(tmp_path))

    messages = [
        _AssistantMessage([_TextBlock("first reply")]),
        _AssistantMessage([_TextBlock("second reply")]),
        _ResultMessage("second reply", cost=0.01),
    ]

    with patch("claude_agent_sdk.query", return_value=_scripted_query(messages)):
        out = await SdkAgentExecutor().run(
            _inv(auth_mode="api_key", prompt="hi there"),
            config={"configurable": {}, "_test_writer": lambda _e: None},
        )

    assert out.is_error is False
    # User prompt is turn 0; each assistant turn appended in order.
    assert out.messages == [
        {"role": "user", "content": "hi there"},
        {"role": "assistant", "content": "first reply"},
        {"role": "assistant", "content": "second reply"},
    ]
    # api_key mode does NOT populate session_id / session_blob_path.
    assert out.session_id is None
    assert out.session_blob_path is None


@pytest.mark.asyncio
async def test_api_key_mode_drops_tool_use_only_assistant_turns(tmp_path: Any, monkeypatch: Any) -> None:
    """Assistant turns with no text blocks (only tool_use) are dropped from messages."""
    monkeypatch.setenv("ATHANOR_WORKSPACE_ROOT", str(tmp_path))

    messages = [
        # Pure tool-call turn → omitted from the Messages-API replay buffer.
        _AssistantMessage([_ToolUseBlock("Read")]),
        _AssistantMessage([_TextBlock("done reading")]),
        _ResultMessage("done", cost=0.0),
    ]

    with patch("claude_agent_sdk.query", return_value=_scripted_query(messages)):
        out = await SdkAgentExecutor().run(
            _inv(auth_mode="api_key", prompt="please read"),
            config={"configurable": {}, "_test_writer": lambda _e: None},
        )

    assert out.messages == [
        {"role": "user", "content": "please read"},
        {"role": "assistant", "content": "done reading"},
    ]


@pytest.mark.asyncio
async def test_api_key_mode_concatenates_multiple_text_blocks(tmp_path: Any, monkeypatch: Any) -> None:
    """Multiple text blocks within one assistant turn collapse to a single content string."""
    monkeypatch.setenv("ATHANOR_WORKSPACE_ROOT", str(tmp_path))

    messages = [
        _AssistantMessage([_TextBlock("part one "), _TextBlock("part two")]),
        _ResultMessage("done", cost=0.0),
    ]

    with patch("claude_agent_sdk.query", return_value=_scripted_query(messages)):
        out = await SdkAgentExecutor().run(
            _inv(auth_mode="api_key"),
            config={"configurable": {}, "_test_writer": lambda _e: None},
        )

    assert out.messages == [
        {"role": "user", "content": "say hello"},
        {"role": "assistant", "content": "part one part two"},
    ]


@pytest.mark.asyncio
async def test_oauth_mode_captures_session_id_and_blob_path(tmp_path: Any, monkeypatch: Any) -> None:
    """oauth/claude_max mode: session_id snapshotted, blob_path discovered via find_session_file."""
    monkeypatch.setenv("ATHANOR_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    sid = "11111111-2222-3333-4444-555555555555"

    # Pre-create the session file at the canonical projects path so find_session_file discovers it.
    from athanor.agents.claude_cli_session import sanitize_cwd_for_session_dir

    session_dir = tmp_path / ".claude" / "projects" / sanitize_cwd_for_session_dir(str(workspace))
    session_dir.mkdir(parents=True)
    session_file = session_dir / f"{sid}.jsonl"
    session_file.write_text("{}")

    messages = [
        _AssistantMessage([_TextBlock("ok")], session_id=sid),
        _ResultMessage("ok", cost=0.001, session_id=sid),
    ]

    with patch("claude_agent_sdk.query", return_value=_scripted_query(messages)):
        out = await SdkAgentExecutor().run(
            _inv(auth_mode="oauth"),
            config={"configurable": {}, "_test_writer": lambda _e: None},
        )

    assert out.is_error is False
    assert out.session_id == sid
    assert out.session_blob_path == str(session_file)
    # oauth mode does NOT populate the messages list — that's the api_key
    # resume path; oauth resumes via the CLI's --resume <id> instead.
    assert out.messages is None


@pytest.mark.asyncio
async def test_oauth_mode_picks_session_id_from_first_carrier(tmp_path: Any, monkeypatch: Any) -> None:
    """First message that carries a session_id wins; later mismatches don't overwrite."""
    monkeypatch.setenv("ATHANOR_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    first_sid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    second_sid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    # Pre-create the session file for first_sid so find_session_file discovers it.
    from athanor.agents.claude_cli_session import sanitize_cwd_for_session_dir

    session_dir = tmp_path / ".claude" / "projects" / sanitize_cwd_for_session_dir(str(workspace))
    session_dir.mkdir(parents=True)
    first_session_file = session_dir / f"{first_sid}.jsonl"
    first_session_file.write_text("{}")

    messages = [
        _AssistantMessage([_TextBlock("hi")], session_id=first_sid),
        _AssistantMessage([_TextBlock("again")], session_id=second_sid),
        _ResultMessage("ok", cost=0.0, session_id=second_sid),
    ]

    with patch("claude_agent_sdk.query", return_value=_scripted_query(messages)):
        out = await SdkAgentExecutor().run(
            _inv(auth_mode="oauth"),
            config={"configurable": {}, "_test_writer": lambda _e: None},
        )

    assert out.session_id == first_sid
    assert out.session_blob_path is not None
    assert out.session_blob_path.endswith(
        f"/projects/{sanitize_cwd_for_session_dir(str(workspace))}/{first_sid}.jsonl"
    )


@pytest.mark.asyncio
async def test_oauth_mode_without_session_id_emits_no_blob_path(tmp_path: Any, monkeypatch: Any) -> None:
    """If the SDK emits no session_id, AgentOutput holds None for both fields."""
    monkeypatch.setenv("ATHANOR_WORKSPACE_ROOT", str(tmp_path))

    messages = [
        _AssistantMessage([_TextBlock("hi")]),  # session_id=None default
        _ResultMessage("hi", cost=0.0),
    ]

    with patch("claude_agent_sdk.query", return_value=_scripted_query(messages)):
        out = await SdkAgentExecutor().run(
            _inv(auth_mode="oauth"),
            config={"configurable": {}, "_test_writer": lambda _e: None},
        )

    assert out.is_error is False
    assert out.session_id is None
    assert out.session_blob_path is None


@pytest.mark.asyncio
async def test_error_path_omits_session_state(tmp_path: Any, monkeypatch: Any) -> None:
    """A failed turn returns ``messages=None`` / ``session_id=None``: no half-state to persist."""
    monkeypatch.setenv("ATHANOR_WORKSPACE_ROOT", str(tmp_path))

    async def raising_query(**_kw: Any):
        # SDK raises before yielding anything → executor catches → is_error.
        raise RuntimeError("SDK boom")
        yield  # pragma: no cover — generator marker

    with patch("claude_agent_sdk.query", side_effect=raising_query):
        out = await SdkAgentExecutor().run(
            _inv(auth_mode="api_key"),
            config={"configurable": {}, "_test_writer": lambda _e: None},
        )

    assert out.is_error is True
    assert out.messages is None
    assert out.session_id is None
    assert out.session_blob_path is None


@pytest.mark.asyncio
async def test_oauth_error_path_omits_session_state(tmp_path: Any, monkeypatch: Any) -> None:
    """Same negative as above but for oauth mode — also omits session_id."""
    monkeypatch.setenv("ATHANOR_WORKSPACE_ROOT", str(tmp_path))

    async def raising_query(**_kw: Any):
        raise RuntimeError("nope")
        yield  # pragma: no cover

    with patch("claude_agent_sdk.query", side_effect=raising_query):
        out = await SdkAgentExecutor().run(
            _inv(auth_mode="oauth"),
            config={"configurable": {}, "_test_writer": lambda _e: None},
        )

    assert out.is_error is True
    assert out.session_id is None
    assert out.session_blob_path is None


@pytest.mark.asyncio
async def test_oauth_mode_emits_none_when_session_file_does_not_exist(tmp_path: Any, monkeypatch: Any) -> None:
    """If the SDK emitted a session_id but the on-disk file isn't where
    discovery looks, session_blob_path is None — we don't lie about
    where the file is. The runner will skip the docker cp."""
    monkeypatch.setenv("ATHANOR_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    sid = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    messages = [
        _AssistantMessage([_TextBlock("ok")], session_id=sid),
        _ResultMessage("ok", cost=0.0, session_id=sid),
    ]
    # Note: NO session file pre-created → find_session_file returns None.

    with patch("claude_agent_sdk.query", return_value=_scripted_query(messages)):
        out = await SdkAgentExecutor().run(
            _inv(auth_mode="oauth"),
            config={"configurable": {}, "_test_writer": lambda _e: None},
        )

    assert out.is_error is False
    assert out.session_id == sid  # SDK still emitted it
    assert out.session_blob_path is None  # but discovery found nothing
