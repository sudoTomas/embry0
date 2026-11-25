"""Stopping a sandbox's agent runner that already exited is "nothing to stop", not an error (#690).

``pkill`` exits 1, printing nothing, when no process matched; the orchestrator's restart cleanup and the agent
runner's stop meet that every time a runner already exited. It is logged at debug. A failure that says something
(the container gone, the daemon unreachable) or another exit stays an error.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from structlog.testing import capture_logs

from embry0.execution.docker_client import PKILL_NOTHING_MATCHED, DockerClient
from tests.unit.execution.test_docker_client import _Pipe, _Proc

PKILL = ["docker", "exec", "sandbox-job-1", "pkill", "-f", "embry0.sandbox.runner"]


def _levels(logs: list[dict[str, Any]]) -> list[str]:
    return [entry["log_level"] for entry in logs if entry["event"] == "docker_cmd_failed"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "level"),
    [
        (1, b"", b"", "debug"),
        (1, b"", b"Error response from daemon: No such container: sandbox-job-1\n", "error"),
        (1, b"some output\n", b"", "error"),
        (2, b"", b"", "error"),
        (126, b"", b"", "error"),
    ],
    ids=["nothing-matched", "no-such-container", "output", "pkill-syntax", "not-executable"],
)
async def test_only_a_silent_quiet_exit_is_logged_at_debug(
    returncode: int, stdout: bytes, stderr: bytes, level: str
) -> None:
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as proc, capture_logs() as logs:
        proc.return_value = _Proc(_Pipe(data=stdout), _Pipe(data=stderr), returncode=returncode)
        with pytest.raises(RuntimeError):
            await DockerClient().run_cmd(PKILL, quiet_exits=(PKILL_NOTHING_MATCHED,))
    assert _levels(logs) == [level], logs


@pytest.mark.asyncio
async def test_without_quiet_exits_a_silent_failure_is_still_an_error() -> None:
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as proc, capture_logs() as logs:
        proc.return_value = _Proc(_Pipe(), _Pipe(), returncode=1)
        with pytest.raises(RuntimeError):
            await DockerClient().run_cmd(PKILL)
    assert _levels(logs) == ["error"]


async def _stop_agent(docker: DockerClient) -> bool:
    from embry0.execution.sandbox_manager import SandboxManager

    return await SandboxManager(docker=docker, proxy_manager=MagicMock()).stop_agent("sandbox-job-1")


async def _stop_runner(docker: DockerClient) -> bool:
    from embry0.execution.agent_runner import AgentRunner

    return await AgentRunner(sandbox_manager=AsyncMock(), docker=docker)._stop_runner_in_sandbox("sandbox-job-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", [_stop_agent, _stop_runner], ids=["restart-cleanup", "agent-runner"])
async def test_stopping_a_runner_that_already_exited_logs_no_error(stop: Any) -> None:
    """Through the real client: what each production caller sends, and what it logs when nothing matched."""
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as proc, capture_logs() as logs:
        proc.return_value = _Proc(_Pipe(), _Pipe(), returncode=PKILL_NOTHING_MATCHED)
        assert await stop(DockerClient()) is False, "nothing was stopped"
    assert proc.await_args.args[-3:] == ("pkill", "-f", "embry0.sandbox.runner")
    assert _levels(logs) == ["debug"], logs


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", [_stop_agent, _stop_runner], ids=["restart-cleanup", "agent-runner"])
async def test_a_runner_stop_the_daemon_refuses_is_still_an_error(stop: Any) -> None:
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as proc, capture_logs() as logs:
        proc.return_value = _Proc(_Pipe(), _Pipe(data=b"Error response from daemon: No such container\n"), returncode=1)
        assert await stop(DockerClient()) is False
    assert _levels(logs) == ["error"], logs
