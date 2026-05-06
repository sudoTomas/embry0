"""AgentRunner / AgentOutput unit tests.

Includes a coverage of the post-2026-05-06 stderr-capture behavior: when
the in-sandbox runner subprocess exits non-zero with no parseable stdout
(e.g. it crashed during import), the orchestrator must surface the
subprocess's stderr in ``AgentOutput.error_message`` so the failure is
diagnosable from the QA dashboard / logs instead of appearing as the
opaque "No final result received from sandbox".
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from athanor.execution.agent_runner import AgentOutput, AgentRunner


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

    async def _stream_exec(*, container: str, command: list[str], workdir: str | None = None) -> _FakeProc:
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
    stderr = b"Traceback (most recent call last):\n  File \"runner.py\", line 9\n    boom\nNameError: boom\n"
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
