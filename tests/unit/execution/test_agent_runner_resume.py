"""AgentRunner.run with ``resume_session`` (Plan C Task 5).

Verifies that when a prior session is supplied:
- For ``anthropic_api`` mode the messages are staged through
  ``sandbox_files`` (written as root into a root-only drop, owned by the
  agent) and ``--session-blob <staged path>`` is appended to the runner's
  docker exec command.
- For ``claude_max`` mode the session bytes are staged the same way and then
  copied, as the agent, to the CLI's canonical session path, and
  ``--session-id <id>`` is appended.
- The legacy path (no resume_session) stages nothing and appends no
  --session-* args.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.agents.session import AgentSession
from embry0.execution.agent_runner import AgentRunner

#: What the staging fake returns for a drop write.
DROP = "/run/embry0/drop/deadbeef/blob"


class _FakeStdout:
    """Async-iterable stdout that yields a single final_result line."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._lines = [(json.dumps({"type": "final_result", **payload}) + "\n").encode()]

    def __aiter__(self) -> _FakeStdout:
        return self

    async def __anext__(self) -> bytes:
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeStderr:
    """Async ``read(n)`` returning empty bytes (EOF) — no stderr produced."""

    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self._chunks = list(chunks or [])

    async def read(self, _n: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _FakeProc:
    def __init__(self, payload: dict[str, Any], stderr_chunks: list[bytes] | None = None, returncode: int = 0) -> None:
        self.stdout = _FakeStdout(payload) if payload else _EmptyStdout()
        self.stderr = _FakeStderr(stderr_chunks)
        self.returncode = returncode

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:  # pragma: no cover — only used by timeout path
        pass


class _EmptyStdout:
    def __aiter__(self) -> _EmptyStdout:
        return self

    async def __anext__(self) -> bytes:
        raise StopAsyncIteration


def _make_runner(stream_payload: dict[str, Any]) -> tuple[AgentRunner, AsyncMock, list[list[str]]]:
    """Build an AgentRunner with mocked DockerClient + capture exec command."""
    docker = AsyncMock()

    # build_exec_cmd is synchronous; echo user so run_cmd can tell a root
    # staging write from an agent-level placement.
    def _build_exec_cmd(container: str, command: list[str], **kw: Any) -> list[str]:
        user = ["-u", kw["user"]] if kw.get("user") else []
        return ["docker", "exec", *user, container, *command]

    docker.build_exec_cmd = MagicMock(side_effect=_build_exec_cmd)

    staged: list[tuple[bytes, bool]] = []  # (bytes, as_root)
    placed: list[str] = []

    async def _run_cmd(cmd: list[str], timeout: int = 0, stdin_data: bytes | None = None) -> str:
        if "python3" in cmd:  # a sandbox_files staging write
            staged.append((stdin_data or b"", cmd[:4] == ["docker", "exec", "-u", "root"]))
            return DROP
        if "bash" in cmd:  # the agent-level placement copy
            placed.append(cmd[-1])
        return ""

    docker.run_cmd = AsyncMock(side_effect=_run_cmd)
    docker.staged = staged
    docker.placed = placed

    captured_commands: list[list[str]] = []

    async def _stream_exec(*, container, command, workdir=None, env=None, stdin_data=None) -> _FakeProc:
        captured_commands.append(list(command))
        return _FakeProc(stream_payload)

    docker.stream_exec = AsyncMock(side_effect=_stream_exec)
    runner = AgentRunner(sandbox_manager=AsyncMock(), docker=docker)
    return runner, docker, captured_commands


@pytest.mark.asyncio
async def test_resume_session_anthropic_api_copies_blob_and_passes_arg(tmp_path: Any) -> None:
    """anthropic_api mode: messages staged for the agent, --session-blob at the staged path."""
    prior_messages = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "ok"}]
    session = AgentSession(
        job_id="J1",
        agent_type="developer",
        mode="anthropic_api",
        messages=prior_messages,
    )

    runner, docker, captured_commands = _make_runner(
        {
            "agent_type": "developer",
            "is_error": False,
            "output": "done",
            "cost_usd": 0.01,
            "duration_ms": 5,
            "tools_called": {},
        }
    )

    result = await runner.run(
        container="sandbox-J1",
        config={"agent_type": "developer", "timeout_seconds": 10},
        resume_session=session,
    )

    # The messages were staged as root (not copied to an agent path); no placement.
    assert docker.staged == [(json.dumps(prior_messages).encode(), True)]
    assert docker.placed == []

    # The runner exec command points --session-blob at the staged (drop) path.
    assert len(captured_commands) == 1
    cmd = captured_commands[0]
    assert "--session-blob" in cmd
    assert cmd[cmd.index("--session-blob") + 1] == DROP
    assert "--session-id" not in cmd

    # Successful round-trip — final_result was parsed and returned.
    assert result.is_error is False
    assert result.output == "done"


@pytest.mark.asyncio
async def test_resume_session_claude_max_copies_bytes_and_passes_session_id() -> None:
    """claude_max mode: blob staged, then placed at the canonical CLI path as the agent, --session-id arg."""
    blob = b'{"some": "jsonl"}\n'
    session = AgentSession(
        job_id="J2",
        agent_type="developer",
        mode="claude_max",
        session_id="sess-abc",
        session_blob=blob,
    )

    runner, docker, captured_commands = _make_runner(
        {
            "agent_type": "developer",
            "is_error": False,
            "output": "ok",
            "cost_usd": 0.0,
            "duration_ms": 1,
            "tools_called": {},
        }
    )

    await runner.run(
        container="sandbox-J2",
        config={"agent_type": "developer", "timeout_seconds": 10},
        resume_session=session,
    )

    # The blob was staged as root, then placed at the canonical session path
    # by an agent-level copy (no root copy to an agent-chosen path).
    assert docker.staged == [(blob, True)]
    assert len(docker.placed) == 1
    place = docker.placed[0]
    assert DROP in place and "/home/agent/.claude/projects/-workspace/sess-abc.jsonl" in place
    assert "cp " in place and "mkdir -p" in place

    cmd = captured_commands[0]
    assert "--session-id" in cmd
    assert cmd[cmd.index("--session-id") + 1] == "sess-abc"
    assert "--session-blob" not in cmd


@pytest.mark.asyncio
async def test_no_resume_session_is_legacy_no_op() -> None:
    """resume_session=None → no copy_*, no --session-* args (back-compat)."""
    runner, docker, captured_commands = _make_runner(
        {
            "agent_type": "developer",
            "is_error": False,
            "output": "",
            "cost_usd": 0.0,
            "duration_ms": 0,
            "tools_called": {},
        }
    )

    await runner.run(
        container="sandbox-J3",
        config={"agent_type": "developer", "timeout_seconds": 10},
    )

    assert docker.staged == [] and docker.placed == []

    cmd = captured_commands[0]
    assert "--session-blob" not in cmd
    assert "--session-id" not in cmd


@pytest.mark.asyncio
async def test_final_result_carries_through_session_fields() -> None:
    """When the sandbox emits messages/session_id/session_blob_path, AgentOutput carries them."""
    runner, _docker, _captured = _make_runner(
        {
            "agent_type": "developer",
            "is_error": False,
            "output": "done",
            "cost_usd": 0.0,
            "duration_ms": 0,
            "tools_called": {},
            "messages": [{"role": "user", "content": "hi"}],
            "session_id": "sess-xyz",
            "session_blob_path": "/home/agent/.claude/sessions/sess-xyz.jsonl",
        }
    )

    result = await runner.run(
        container="sandbox-J4",
        config={"agent_type": "developer", "timeout_seconds": 10},
    )

    assert result.messages == [{"role": "user", "content": "hi"}]
    assert result.session_id == "sess-xyz"
    assert result.session_blob_path == "/home/agent/.claude/sessions/sess-xyz.jsonl"


# ---------------------------------------------------------------------------
# Plan C closeout: post-run session-blob extraction.
#
# When the sandbox runner reports session_id + session_blob_path (claude_max
# / oauth mode), the AgentRunner must read the JSONL bytes out of the sandbox
# (as root, refusing a symlink) BEFORE the caller destroys it, and populate
# AgentOutput.session_blob so the upstream AgentSessionsRepository can persist
# them and the next turn can stage them back into a fresh sandbox.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_run_extracts_session_blob_when_session_id_present() -> None:
    """oauth mode: AgentRunner copies the in-sandbox jsonl out and sets session_blob."""
    runner, docker, _captured = _make_runner(
        {
            "agent_type": "developer",
            "is_error": False,
            "output": "done",
            "cost_usd": 0.0,
            "duration_ms": 0,
            "tools_called": {},
            "session_id": "sess-pull",
            "session_blob_path": "/home/agent/.claude/sessions/sess-pull.jsonl",
        }
    )

    expected_bytes = b'{"role":"assistant","content":"hi"}\n'
    docker.run_cmd_bytes = AsyncMock(return_value=expected_bytes)

    result = await runner.run(
        container="sandbox-extract",
        config={"agent_type": "developer", "timeout_seconds": 10},
    )

    docker.run_cmd_bytes.assert_awaited_once()
    read_cmd = docker.run_cmd_bytes.await_args.args[0]
    assert read_cmd[:4] == ["docker", "exec", "-u", "agent"], "the session file is read as the agent (it owns it)"
    assert "/home/agent/.claude/sessions/sess-pull.jsonl" in read_cmd
    assert result.session_id == "sess-pull"
    assert result.session_blob == expected_bytes


@pytest.mark.asyncio
async def test_post_run_skips_extract_when_session_id_missing() -> None:
    """api_key mode (no session_id): no docker-cp out, no session_blob."""
    runner, docker, _captured = _make_runner(
        {
            "agent_type": "developer",
            "is_error": False,
            "output": "done",
            "cost_usd": 0.0,
            "duration_ms": 0,
            "tools_called": {},
            "messages": [{"role": "user", "content": "hi"}],
            # session_id / session_blob_path absent — api_key path.
        }
    )

    docker.run_cmd_bytes = AsyncMock()

    result = await runner.run(
        container="sandbox-noextract",
        config={"agent_type": "developer", "timeout_seconds": 10},
    )

    docker.run_cmd_bytes.assert_not_awaited()
    assert result.session_blob is None
    assert result.messages == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_post_run_extract_failure_is_best_effort() -> None:
    """If docker cp fails (race / wrong path), the run still returns; session_blob stays None."""
    runner, docker, _captured = _make_runner(
        {
            "agent_type": "developer",
            "is_error": False,
            "output": "done",
            "cost_usd": 0.0,
            "duration_ms": 0,
            "tools_called": {},
            "session_id": "sess-gone",
            "session_blob_path": "/home/agent/.claude/sessions/sess-gone.jsonl",
        }
    )

    docker.run_cmd_bytes = AsyncMock(side_effect=RuntimeError("no such file"))

    result = await runner.run(
        container="sandbox-fail-extract",
        config={"agent_type": "developer", "timeout_seconds": 10},
    )

    docker.run_cmd_bytes.assert_awaited_once()
    # Run completes, session metadata still threaded through.
    assert result.is_error is False
    assert result.session_id == "sess-gone"
    # blob is None because the read raised — best-effort.
    assert result.session_blob is None


@pytest.mark.asyncio
async def test_post_run_skips_extract_when_errored_run_has_no_session_fields() -> None:
    """Ordinary error: the executor omits session fields, so nothing is read out."""
    runner, docker, _captured = _make_runner(
        {
            "agent_type": "developer",
            "is_error": True,
            "error_message": "boom",
            "output": "",
            "cost_usd": 0.0,
            "duration_ms": 0,
            "tools_called": {},
        }
    )

    docker.run_cmd_bytes = AsyncMock()

    result = await runner.run(
        container="sandbox-errored",
        config={"agent_type": "developer", "timeout_seconds": 10},
    )

    docker.run_cmd_bytes.assert_not_awaited()
    assert result.is_error is True
    assert result.session_blob is None


@pytest.mark.asyncio
async def test_post_run_extracts_when_errored_run_reports_session_fields() -> None:
    """RAV-1260: the executor is the authority on whether an errored run's
    session is worth keeping — it only sets session_id/blob path for turn-cap
    deaths, and the runner must extract the blob so the decide-now recovery
    can resume the conversation."""
    runner, docker, _captured = _make_runner(
        {
            "agent_type": "review",
            "is_error": True,
            "error_message": "Reached maximum number of turns (25)",
            "output": "narration",
            "cost_usd": 0.2,
            "duration_ms": 0,
            "tools_called": {},
            "session_id": "sess-cap",
            "session_blob_path": "/home/agent/.claude/sessions/sess-cap.jsonl",
        }
    )

    docker.run_cmd_bytes = AsyncMock(return_value=b"jsonl-bytes")

    result = await runner.run(
        container="sandbox-capped",
        config={"agent_type": "review", "timeout_seconds": 10},
    )

    docker.run_cmd_bytes.assert_awaited_once()
    assert result.is_error is True
    assert result.session_id == "sess-cap"
    assert result.session_blob == b"jsonl-bytes"
