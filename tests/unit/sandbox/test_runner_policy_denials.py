"""#470: policy denials counted in the sandbox reach the orchestrator.

The in-sandbox executor counts denials on ``AgentOutput.policy_denials``;
the sandbox runner serializes the result as its ``final_result`` line; the
orchestrator's AgentRunner parses that line back into an AgentOutput. The
hand-off in the middle used to drop the field, so the orchestrator always
saw ``{}``.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from embry0.execution.agent_runner import AgentOutput, AgentRunner
from embry0.sandbox.runner import run_agent
from tests.fixtures.sandbox_runner import as_the_runner

DENIALS = {"Read denied by rule Read(//etc/**)": 2, "Bash denied by rule Bash(curl:*)": 1}


class _ExecutorThatWasDenied:
    async def run(self, invocation: Any, _config: Any, **_kwargs: Any) -> AgentOutput:
        return AgentOutput(agent_type=invocation.agent_type, output="done", policy_denials=dict(DENIALS))


class _Lines:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    def __aiter__(self) -> _Lines:
        return self

    async def __anext__(self) -> bytes:
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _NoStderr:
    async def read(self, _n: int) -> bytes:
        return b""


class _Proc:
    def __init__(self, line: bytes) -> None:
        self.stdout = _Lines([line])
        self.stderr = _NoStderr()
        self.returncode = 0

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:  # pragma: no cover — timeout path only
        pass


@pytest.mark.asyncio
async def test_policy_denials_cross_the_sandbox_boundary() -> None:
    with patch("embry0.sandbox.runner.select_executor", return_value=_ExecutorThatWasDenied()):
        result = await run_agent({"agent_type": "developer", "prompt": "fix it"})
    line = (json.dumps({"type": "final_result", **result}, default=str) + "\n").encode()

    docker = AsyncMock()
    docker.stream_exec = AsyncMock(side_effect=lambda **kw: as_the_runner(_Proc(line), kw.get("stdin_data")))
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec", "c", "true"])
    runner = AgentRunner(sandbox_manager=AsyncMock(), docker=docker)

    output = await runner.run("c1", {"agent_type": "developer", "prompt": "fix it"})

    assert output.policy_denials == DENIALS, (
        f"denials counted in the sandbox must reach AgentOutput.policy_denials, got {output.policy_denials!r}"
    )
