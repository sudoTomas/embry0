"""AgentRunner / AgentOutput unit tests.

Includes a coverage of the post-2026-05-06 stderr-capture behavior: when
the in-sandbox runner subprocess exits non-zero with no parseable stdout
(e.g. it crashed during import), the orchestrator must surface the
subprocess's stderr in ``AgentOutput.error_message`` so the failure is
diagnosable from the QA dashboard / logs instead of appearing as the
opaque "No final result received from sandbox".
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.execution.agent_runner import AgentOutput, AgentRunner


def test_agent_output_dataclass():
    output = AgentOutput(
        agent_type="developer",
        is_error=False,
        output="Fixed the bug",
        cost_usd=0.42,
        duration_ms=15000,
        tools_called={"Read": 5, "Edit": 2},
    )
    assert output.agent_type == "developer"
    assert output.cost_usd == 0.42
    assert not output.is_error


def test_agent_output_defaults():
    output = AgentOutput(agent_type="test")
    assert output.is_error is False
    assert output.error_message == ""
    assert output.output == ""
    assert output.cost_usd == 0.0
    assert output.duration_ms == 0
    assert output.tools_called == {}


# ---------------------------------------------------------------------------
# stderr capture on subprocess failure
# ---------------------------------------------------------------------------


class _EmptyStdout:
    def __aiter__(self) -> _EmptyStdout:
        return self

    async def __anext__(self) -> bytes:
        raise StopAsyncIteration


class _ChunkedStderr:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    async def read(self, _n: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _FakeProc:
    def __init__(self, *, returncode: int, stderr_chunks: list[bytes]) -> None:
        self.stdout = _EmptyStdout()
        self.stderr = _ChunkedStderr(stderr_chunks)
        self.returncode = returncode

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:  # pragma: no cover — only used by timeout
        pass


def _runner_with_proc(proc: _FakeProc) -> AgentRunner:
    docker = AsyncMock()

    async def _stream_exec(
        *, container: str, command: list[str], workdir: str | None = None, env: dict[str, str] | None = None
    ) -> _FakeProc:
        return proc

    docker.stream_exec = AsyncMock(side_effect=_stream_exec)
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec", "c", "true"])
    return AgentRunner(sandbox_manager=AsyncMock(), docker=docker)


@pytest.mark.asyncio
async def test_runner_surfaces_stderr_when_subprocess_exits_nonzero_with_no_stdout() -> None:
    """Reproduces the 2026-05-06 QA-pipeline silent-fail.

    The sandbox runner produced 0 stdout lines and exited 1; the orchestrator
    only saw the opaque error_message="No final result received from sandbox"
    and could not tell *why*. Now the captured stderr (truncated) must be
    appended to error_message.
    """
    stderr = b'Traceback (most recent call last):\n  File "runner.py", line 9\n    boom\nNameError: boom\n'
    proc = _FakeProc(returncode=1, stderr_chunks=[stderr])
    runner = _runner_with_proc(proc)

    out = await runner.run(
        container="c",
        config={"agent_type": "qa", "timeout_seconds": 5},
    )

    assert out.is_error is True
    assert "No final result received from sandbox" in out.error_message
    assert "NameError: boom" in out.error_message, out.error_message


@pytest.mark.asyncio
async def test_runner_does_not_append_stderr_when_empty() -> None:
    """When stderr is empty, error_message stays clean (no trailing semicolon)."""
    proc = _FakeProc(returncode=1, stderr_chunks=[])
    runner = _runner_with_proc(proc)

    out = await runner.run(
        container="c",
        config={"agent_type": "qa", "timeout_seconds": 5},
    )

    assert out.is_error is True
    assert out.error_message == "No final result received from sandbox"


# ---------------------------------------------------------------------------
# EMB-37: per-tool-call timeout env + idle watchdog
# ---------------------------------------------------------------------------


class _HangingStdout:
    """Yields one line, then hangs until kill() releases it as EOF —
    simulates a run whose tool call hung mid-stream."""

    def __init__(self) -> None:
        self._released = asyncio.Event()
        self._sent = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if not self._sent:
            self._sent = True
            return b'{"type": "agent_started"}\n'
        await self._released.wait()
        raise StopAsyncIteration

    def release(self) -> None:
        self._released.set()


class _HangingProc:
    def __init__(self) -> None:
        self.stdout = _HangingStdout()
        self.stderr = _ChunkedStderr([])
        self.returncode = -9
        self.killed = False

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.stdout.release()


@pytest.mark.asyncio
async def test_runner_passes_tool_timeout_env_to_stream_exec() -> None:
    """MCP_TOOL_TIMEOUT / BASH_*_TIMEOUT_MS must reach the docker exec env —
    the CLI child inherits them and imposes per-tool-call caps (EMB-37)."""
    proc = _FakeProc(returncode=0, stderr_chunks=[])
    runner = _runner_with_proc(proc)
    await runner.run(container="c", config={"agent_type": "qa", "timeout_seconds": 5})

    docker = runner._docker
    kwargs = docker.stream_exec.call_args.kwargs
    env = kwargs.get("env") or {}
    assert env.get("MCP_TOOL_TIMEOUT") == "120000"
    assert env.get("BASH_DEFAULT_TIMEOUT_MS") == "120000"
    assert env.get("BASH_MAX_TIMEOUT_MS") == "240000"


@pytest.mark.asyncio
async def test_runner_idle_watchdog_kills_silent_run() -> None:
    """A run that stops producing stdout events gets killed at the idle
    grace and reports a distinct idle error, not the generic no-result."""
    proc = _HangingProc()
    runner = _runner_with_proc(proc)

    out = await runner.run(
        container="c",
        config={"agent_type": "qa", "timeout_seconds": 30, "idle_grace_seconds": 0.3},
    )
    assert proc.killed is True
    assert out.is_error is True
    assert "idle watchdog" in out.error_message


@pytest.mark.asyncio
async def test_runner_heartbeats_suppress_idle_watchdog() -> None:
    """Steady stdout lines keep the watchdog quiet; the run completes
    normally with its final_result."""
    lines = [b'{"type": "agent_started"}\n'] * 3 + [
        b'{"type": "final_result", "agent_type": "qa", "is_error": false, "output": "done"}\n'
    ]

    class _SlowStdout:
        def __init__(self) -> None:
            self._lines = list(lines)

        def __aiter__(self):
            return self

        async def __anext__(self) -> bytes:
            if not self._lines:
                raise StopAsyncIteration
            await asyncio.sleep(0.15)  # each gap < grace, total > grace
            return self._lines.pop(0)

    proc = _FakeProc(returncode=0, stderr_chunks=[])
    proc.stdout = _SlowStdout()
    runner = _runner_with_proc(proc)

    out = await runner.run(
        container="c",
        config={"agent_type": "qa", "timeout_seconds": 30, "idle_grace_seconds": 0.4},
    )
    assert out.is_error is False
    assert out.output == "done"


@pytest.mark.asyncio
async def test_runner_reconstructs_token_fields_from_final_result() -> None:
    """EMB-35: token counts in the final_result wire payload land on AgentOutput."""
    line = (
        json.dumps(
            {
                "type": "final_result",
                "agent_type": "developer",
                "is_error": False,
                "output": "done",
                "cost_usd": 0.03,
                "duration_ms": 1200,
                "input_tokens": 1500,
                "output_tokens": 250,
                "cache_read_tokens": 88000,
                "cache_creation_tokens": 3000,
            }
        ).encode()
        + b"\n"
    )

    class _OneLineStdout:
        def __init__(self) -> None:
            self._lines = [line]

        def __aiter__(self):
            return self

        async def __anext__(self) -> bytes:
            if not self._lines:
                raise StopAsyncIteration
            return self._lines.pop(0)

    proc = _FakeProc(returncode=0, stderr_chunks=[])
    proc.stdout = _OneLineStdout()
    runner = _runner_with_proc(proc)

    out = await runner.run(
        container="c",
        config={"agent_type": "developer", "timeout_seconds": 30},
    )
    assert out.input_tokens == 1500
    assert out.output_tokens == 250
    assert out.cache_read_tokens == 88000
    assert out.cache_creation_tokens == 3000


@pytest.mark.asyncio
async def test_runner_token_fields_default_zero_on_legacy_payload() -> None:
    """A final_result without token keys (pre-EMB-35 sandbox image) yields zeros."""
    line = b'{"type": "final_result", "agent_type": "qa", "is_error": false, "output": "ok"}\n'

    class _OneLineStdout:
        def __init__(self) -> None:
            self._lines = [line]

        def __aiter__(self):
            return self

        async def __anext__(self) -> bytes:
            if not self._lines:
                raise StopAsyncIteration
            return self._lines.pop(0)

    proc = _FakeProc(returncode=0, stderr_chunks=[])
    proc.stdout = _OneLineStdout()
    runner = _runner_with_proc(proc)

    out = await runner.run(container="c", config={"agent_type": "qa", "timeout_seconds": 30})
    assert out.is_error is False
    assert out.input_tokens == 0
    assert out.output_tokens == 0
    assert out.cache_read_tokens == 0
    assert out.cache_creation_tokens == 0
