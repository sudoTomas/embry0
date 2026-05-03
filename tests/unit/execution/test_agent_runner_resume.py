"""AgentRunner.run with ``resume_session`` (Plan C Task 5).

Verifies that when a prior session is supplied:
- For ``anthropic_api`` mode the messages are dumped to a host tempfile,
  ``DockerClient.copy_into`` is invoked exactly once with the expected
  destination path, and ``--session-blob /tmp/.athanor-resume-blob`` is
  appended to the runner's docker exec command.
- For ``claude_max`` mode the session bytes are streamed in via
  ``copy_bytes_into`` and ``--session-id <id>`` is appended.
- The legacy path (no resume_session) does not call the copy helpers and
  does not append any --session-* args.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from athanor.agents.session import AgentSession
from athanor.execution.agent_runner import RESUME_BLOB_SANDBOX_PATH, AgentRunner


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


class _FakeProc:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.stdout = _FakeStdout(payload)
        self.returncode = 0

    async def wait(self) -> int:
        return 0

    def kill(self) -> None:  # pragma: no cover — only used by timeout path
        pass


def _make_runner(stream_payload: dict[str, Any]) -> tuple[AgentRunner, AsyncMock, list[list[str]]]:
    """Build an AgentRunner with mocked DockerClient + capture exec command."""
    docker = AsyncMock()
    docker.copy_into = AsyncMock()
    docker.copy_bytes_into = AsyncMock()
    docker.run_cmd = AsyncMock(return_value="")

    # build_exec_cmd is a synchronous method; return a predictable sentinel
    # list so that assertions on run_cmd.call_args can inspect the payload.
    def _build_exec_cmd(container: str, command: list[str], workdir: str | None = None, env: dict | None = None) -> list[str]:
        return ["docker", "exec", container, *command]

    docker.build_exec_cmd = MagicMock(side_effect=_build_exec_cmd)

    captured_commands: list[list[str]] = []

    async def _stream_exec(*, container: str, command: list[str], workdir: str | None = None) -> _FakeProc:
        captured_commands.append(list(command))
        return _FakeProc(stream_payload)

    docker.stream_exec = AsyncMock(side_effect=_stream_exec)
    runner = AgentRunner(sandbox_manager=AsyncMock(), docker=docker)
    return runner, docker, captured_commands


@pytest.mark.asyncio
async def test_resume_session_anthropic_api_copies_blob_and_passes_arg(tmp_path: Any) -> None:
    """anthropic_api mode: copy_into once with dumped messages JSON, --session-blob arg."""
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

    # Capture the host-side tempfile path so we can assert its contents
    # before the runner unlinks it. copy_into is awaited inside the
    # try-block; we read the file from the side_effect.
    seen_calls: list[tuple[str, str, list[dict[str, Any]]]] = []

    async def _capture_copy(container: str, src: str, dst: str) -> None:
        with open(src) as f:
            seen_calls.append((container, dst, json.load(f)))

    docker.copy_into.side_effect = _capture_copy

    result = await runner.run(
        container="sandbox-J1",
        config={"agent_type": "developer", "timeout_seconds": 10},
        resume_session=session,
    )

    # Exactly one copy_into call, no copy_bytes_into.
    docker.copy_into.assert_awaited_once()
    docker.copy_bytes_into.assert_not_awaited()

    assert len(seen_calls) == 1
    container, dst, dumped = seen_calls[0]
    assert container == "sandbox-J1"
    assert dst == RESUME_BLOB_SANDBOX_PATH == "/tmp/.athanor-resume-blob"
    assert dumped == prior_messages

    # The runner exec command must include --session-blob <path>
    assert len(captured_commands) == 1
    cmd = captured_commands[0]
    assert "--session-blob" in cmd
    assert cmd[cmd.index("--session-blob") + 1] == "/tmp/.athanor-resume-blob"
    assert "--session-id" not in cmd

    # Successful round-trip — final_result was parsed and returned.
    assert result.is_error is False
    assert result.output == "done"


@pytest.mark.asyncio
async def test_resume_session_claude_max_copies_bytes_and_passes_session_id() -> None:
    """claude_max mode: copy_bytes_into once at the canonical CLI path, --session-id arg."""
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

    docker.copy_into.assert_not_awaited()

    # mkdir -p must have been called (and before copy_bytes_into).
    mkdir_calls = [
        c for c in docker.run_cmd.call_args_list if "mkdir" in c.args[0]
    ]
    assert len(mkdir_calls) == 1
    assert "/home/agent/.claude/projects/-workspace" in mkdir_calls[0].args[0]

    docker.copy_bytes_into.assert_awaited_once_with(
        "sandbox-J2",
        blob,
        "/home/agent/.claude/projects/-workspace/sess-abc.jsonl",
    )

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

    docker.copy_into.assert_not_awaited()
    docker.copy_bytes_into.assert_not_awaited()

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
# / oauth mode), the AgentRunner must ``docker cp`` the JSONL bytes out of
# the sandbox BEFORE the caller destroys it, and populate AgentOutput.session_blob
# (bytes) so the upstream AgentSessionsRepository can persist them and the
# next turn can re-stream them back into a fresh sandbox via copy_bytes_into.
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
    docker.copy_bytes_from = AsyncMock(return_value=expected_bytes)

    result = await runner.run(
        container="sandbox-extract",
        config={"agent_type": "developer", "timeout_seconds": 10},
    )

    docker.copy_bytes_from.assert_awaited_once_with(
        "sandbox-extract",
        "/home/agent/.claude/sessions/sess-pull.jsonl",
    )
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

    docker.copy_bytes_from = AsyncMock()

    result = await runner.run(
        container="sandbox-noextract",
        config={"agent_type": "developer", "timeout_seconds": 10},
    )

    docker.copy_bytes_from.assert_not_awaited()
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

    docker.copy_bytes_from = AsyncMock(side_effect=RuntimeError("no such file"))

    result = await runner.run(
        container="sandbox-fail-extract",
        config={"agent_type": "developer", "timeout_seconds": 10},
    )

    docker.copy_bytes_from.assert_awaited_once()
    # Run completes, session metadata still threaded through.
    assert result.is_error is False
    assert result.session_id == "sess-gone"
    # blob is None because copy_bytes_from raised — best-effort.
    assert result.session_blob is None


@pytest.mark.asyncio
async def test_post_run_skips_extract_when_run_errored() -> None:
    """is_error=True from the sandbox: do not attempt the post-run docker cp."""
    runner, docker, _captured = _make_runner(
        {
            "agent_type": "developer",
            "is_error": True,
            "error_message": "boom",
            "output": "",
            "cost_usd": 0.0,
            "duration_ms": 0,
            "tools_called": {},
            # Sandbox may still report a session_id / blob path even on
            # error, but the runner should skip extraction since the
            # session is half-formed and not worth resuming.
            "session_id": "sess-half",
            "session_blob_path": "/home/agent/.claude/sessions/sess-half.jsonl",
        }
    )

    docker.copy_bytes_from = AsyncMock()

    result = await runner.run(
        container="sandbox-errored",
        config={"agent_type": "developer", "timeout_seconds": 10},
    )

    docker.copy_bytes_from.assert_not_awaited()
    assert result.is_error is True
    assert result.session_blob is None
