"""EMB-35 session-resume unit tests.

Covers the three layers of the resume mechanism:

- ``session.py`` helpers: ``session_resumable`` (must mirror staging
  exactly), ``render_transcript_block`` (bounded, newest-first survival),
  ``merged_resume_messages`` (fallback data doesn't shrink across resume
  generations).
- ``SdkAgentExecutor.run``: ``resume_session_id`` → ``options.resume``;
  ``resume_messages`` → transcript prepended to the prompt; session-id
  resume wins when both are passed.
- ``AgentRunner._stage_resume_session``: mode-agnostic precedence
  (blob+id → ``--session-id``; messages → ``--session-blob``; neither →
  no args) and the oversized-blob fallthrough.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from embry0.agents.executor import SdkAgentExecutor
from embry0.agents.invocation import AgentInvocation
from embry0.agents.session import (
    MAX_RESUME_BLOB_BYTES,
    AgentSession,
    merged_resume_messages,
    render_transcript_block,
    session_resumable,
)
from embry0.execution.agent_runner import AgentRunner
from embry0.safety.policy import default_policy_for_agent


def _inv(**kw: Any) -> AgentInvocation:
    base: dict[str, Any] = {
        "agent_type": "developer",
        "prompt": "continue",
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


def _session(**kw: Any) -> AgentSession:
    base: dict[str, Any] = {"job_id": "J", "agent_type": "developer", "mode": "anthropic_api"}
    base.update(kw)
    return AgentSession(**base)


class _ResultMessage:
    def __init__(self) -> None:
        self.result = "done"
        self.total_cost_usd = 0.001
        self.duration_ms = 10
        self.num_turns = 1
        self.usage = {"input_tokens": 1, "output_tokens": 1}


async def _one_result(*_a: Any, **_kw: Any):
    yield _ResultMessage()


# ---------------------------------------------------------------------------
# session_resumable
# ---------------------------------------------------------------------------


def test_resumable_none_is_false() -> None:
    assert session_resumable(None) is False


def test_resumable_blob_and_id() -> None:
    assert session_resumable(_session(session_id="s", session_blob=b"x")) is True


def test_resumable_blob_without_id_falls_to_messages() -> None:
    assert session_resumable(_session(session_blob=b"x")) is False
    assert session_resumable(_session(session_blob=b"x", messages=[{"role": "user", "content": "h"}])) is True


def test_resumable_messages_only() -> None:
    assert session_resumable(_session(messages=[{"role": "user", "content": "h"}])) is True


def test_resumable_oversized_blob_without_messages_is_false() -> None:
    big = b"x" * (MAX_RESUME_BLOB_BYTES + 1)
    assert session_resumable(_session(session_id="s", session_blob=big)) is False


def test_resumable_oversized_blob_with_messages_is_true() -> None:
    big = b"x" * (MAX_RESUME_BLOB_BYTES + 1)
    sess = _session(session_id="s", session_blob=big, messages=[{"role": "user", "content": "h"}])
    assert session_resumable(sess) is True


def test_resumable_empty_session_is_false() -> None:
    assert session_resumable(_session()) is False


# ---------------------------------------------------------------------------
# render_transcript_block
# ---------------------------------------------------------------------------


def test_transcript_renders_roles_in_order() -> None:
    block = render_transcript_block(
        [
            {"role": "user", "content": "fix the bug"},
            {"role": "assistant", "content": "done, pushed"},
        ]
    )
    assert "[USER]: fix the bug" in block
    assert "[ASSISTANT]: done, pushed" in block
    assert block.index("[USER]") < block.index("[ASSISTANT]")
    assert "truncated" not in block


def test_transcript_keeps_most_recent_within_budget() -> None:
    msgs = [{"role": "user", "content": f"turn {i}: " + "x" * 500} for i in range(200)]
    block = render_transcript_block(msgs, max_bytes=2_000)
    assert "turn 199" in block  # newest survives
    assert "turn 0:" not in block  # oldest dropped
    assert "(older turns truncated)" in block
    assert len(block.encode()) < 3_000


def test_transcript_single_oversized_message_tail_truncated() -> None:
    block = render_transcript_block([{"role": "assistant", "content": "A" * 10_000 + "THE-END"}], max_bytes=1_000)
    assert "THE-END" in block  # tail survives
    assert len(block.encode()) < 2_000


# ---------------------------------------------------------------------------
# merged_resume_messages
# ---------------------------------------------------------------------------


def test_merge_prepends_prior_when_resume_engaged() -> None:
    prior = [{"role": "user", "content": "original task"}]
    sess = _session(messages=prior)
    new = [{"role": "user", "content": "delta"}, {"role": "assistant", "content": "ok"}]
    assert merged_resume_messages(sess, new) == prior + new


def test_merge_passthrough_when_no_session() -> None:
    new = [{"role": "user", "content": "full run"}]
    assert merged_resume_messages(None, new) == new


def test_merge_passthrough_when_session_not_resumable() -> None:
    # Not resumable ⇒ the run was fresh with a full prompt ⇒ new messages
    # are already complete; merging would duplicate.
    sess = _session()  # nothing usable
    new = [{"role": "user", "content": "full run"}]
    assert merged_resume_messages(sess, new) == new


def test_merge_none_new_messages_stays_none() -> None:
    sess = _session(messages=[{"role": "user", "content": "x"}])
    assert merged_resume_messages(sess, None) is None


# ---------------------------------------------------------------------------
# SdkAgentExecutor resume wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_sets_options_resume(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    captured: dict[str, Any] = {}

    def fake_query(*, prompt: str, options: Any):
        captured["prompt"] = prompt
        captured["options"] = options
        return _one_result()

    with patch("claude_agent_sdk.query", side_effect=fake_query):
        out = await SdkAgentExecutor().run(
            _inv(),
            config={"configurable": {}, "_test_writer": lambda _e: None},
            resume_session_id="sess-123",
        )

    assert out.is_error is False
    assert captured["options"].resume == "sess-123"
    assert "Prior conversation transcript" not in captured["prompt"]


@pytest.mark.asyncio
async def test_executor_prepends_transcript_for_resume_messages(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    captured: dict[str, Any] = {}

    def fake_query(*, prompt: str, options: Any):
        captured["prompt"] = prompt
        captured["options"] = options
        return _one_result()

    with patch("claude_agent_sdk.query", side_effect=fake_query):
        await SdkAgentExecutor().run(
            _inv(prompt="address the feedback"),
            config={"configurable": {}, "_test_writer": lambda _e: None},
            resume_messages=[{"role": "user", "content": "the original task"}],
        )

    assert "Prior conversation transcript" in captured["prompt"]
    assert "the original task" in captured["prompt"]
    # The live instruction comes AFTER the transcript.
    assert captured["prompt"].index("Prior conversation transcript") < captured["prompt"].index("address the feedback")
    assert getattr(captured["options"], "resume", None) is None


@pytest.mark.asyncio
async def test_executor_session_id_resume_wins_over_messages(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    captured: dict[str, Any] = {}

    def fake_query(*, prompt: str, options: Any):
        captured["prompt"] = prompt
        captured["options"] = options
        return _one_result()

    with patch("claude_agent_sdk.query", side_effect=fake_query):
        await SdkAgentExecutor().run(
            _inv(),
            config={"configurable": {}, "_test_writer": lambda _e: None},
            resume_session_id="sess-123",
            resume_messages=[{"role": "user", "content": "old"}],
        )

    assert captured["options"].resume == "sess-123"
    assert "Prior conversation transcript" not in captured["prompt"]


# ---------------------------------------------------------------------------
# AgentRunner._stage_resume_session — mode-agnostic precedence
# ---------------------------------------------------------------------------


def _runner() -> AgentRunner:
    docker = AsyncMock()
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec", "c", "true"])
    return AgentRunner(sandbox_manager=AsyncMock(), docker=docker)


@pytest.mark.asyncio
async def test_stage_none_returns_no_args() -> None:
    runner = _runner()
    assert await runner._stage_resume_session("c", None) == []


@pytest.mark.asyncio
async def test_stage_blob_wins_in_any_mode() -> None:
    """anthropic_api mode with a blob+id now stages the CLI session file —
    the old behavior (messages tempfile) applies only when no blob exists."""
    runner = _runner()
    sess = _session(
        mode="anthropic_api",
        session_id="sess-1",
        session_blob=b'{"jsonl": 1}',
        messages=[{"role": "user", "content": "x"}],
    )
    args = await runner._stage_resume_session("c", sess)
    assert args == ["--session-id", "sess-1"]
    runner._docker.copy_bytes_into.assert_awaited_once()


@pytest.mark.asyncio
async def test_stage_messages_fallback_when_no_blob() -> None:
    runner = _runner()
    sess = _session(mode="claude_max", messages=[{"role": "user", "content": "x"}])
    args = await runner._stage_resume_session("c", sess)
    assert args[0] == "--session-blob"
    runner._docker.copy_into.assert_awaited_once()


@pytest.mark.asyncio
async def test_stage_oversized_blob_falls_to_messages() -> None:
    runner = _runner()
    sess = _session(
        session_id="sess-1",
        session_blob=b"x" * (MAX_RESUME_BLOB_BYTES + 1),
        messages=[{"role": "user", "content": "x"}],
    )
    args = await runner._stage_resume_session("c", sess)
    assert args[0] == "--session-blob"
    runner._docker.copy_bytes_into.assert_not_awaited()


@pytest.mark.asyncio
async def test_stage_oversized_blob_no_messages_no_args() -> None:
    runner = _runner()
    sess = _session(session_id="sess-1", session_blob=b"x" * (MAX_RESUME_BLOB_BYTES + 1))
    assert await runner._stage_resume_session("c", sess) == []
