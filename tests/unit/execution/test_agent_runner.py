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
import structlog.testing

from embry0.execution.agent_runner import AgentOutput, AgentRunner
from embry0.safety.error_codes import ErrorCode


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
        *,
        container: str,
        command: list[str],
        workdir: str | None = None,
        env: dict[str, str] | None = None,
        stdin_data: bytes | None = None,
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
    # #354: zero stdout lines + a traceback is a runner that never started
    # the agent, and says so; the stderr still rides the message.
    assert "could not start the agent" in out.error_message
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


_PKILL = ["pkill", "-f", "embry0.sandbox.runner"]


def _recording_runner(proc: _HangingProc, *, pkill_error: Exception | None = None) -> AgentRunner:
    """A runner whose fake docker builds real exec argv and records every command."""
    runner = _runner_with_proc(proc)
    docker = runner._docker
    docker.build_exec_cmd = MagicMock(
        side_effect=lambda container, command, **_: ["docker", "exec", container, *command]
    )

    async def _run_cmd(cmd: list[str], timeout: int = 60, **_: object) -> str:
        if pkill_error is not None and cmd[-3:] == _PKILL:
            raise pkill_error
        return ""

    docker.run_cmd = AsyncMock(side_effect=_run_cmd)
    return runner


@pytest.mark.asyncio
async def test_wall_clock_timeout_stops_the_agent_inside_the_sandbox() -> None:
    """Killing the docker exec client does not kill the runner inside the
    sandbox, and a timed-out job pauses with its sandbox kept: the agent
    went on working, and spending, unseen, and a resume started a second
    one in the same workspace. The timeout must stop it there and say so."""
    proc = _HangingProc()
    runner = _recording_runner(proc)
    with structlog.testing.capture_logs() as logs:
        out = await runner.run(
            container="sandbox-job-x",
            config={"agent_type": "developer", "timeout_seconds": 0.3, "idle_grace_seconds": 30},
        )
    assert out.is_error and "timed out after" in out.error_message
    assert proc.killed is True
    sent = [c.args[0] for c in runner._docker.run_cmd.await_args_list]
    assert ["docker", "exec", "sandbox-job-x", *_PKILL] in sent, f"the timeout left the agent running; sent {sent}"
    record = [e for e in logs if e["event"] == "agent_runner_wall_clock_timeout"]
    assert record, "the timeout kill is not recorded"
    assert record[0]["container"] == "sandbox-job-x" and record[0]["runner_stopped"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [RuntimeError("pkill exited 1"), TimeoutError("docker command timed out after 10s")],
)
async def test_a_failed_in_sandbox_kill_still_returns_the_timeout(error: Exception) -> None:
    """pkill exits 1 when the runner is already gone, and docker can hang:
    either way the run ends with its timeout, and the record says the kill
    did not land."""
    runner = _recording_runner(_HangingProc(), pkill_error=error)
    with structlog.testing.capture_logs() as logs:
        out = await runner.run(
            container="sandbox-job-x",
            config={"agent_type": "developer", "timeout_seconds": 0.3, "idle_grace_seconds": 30},
        )
    assert "timed out after" in out.error_message
    record = [e for e in logs if e["event"] == "agent_runner_wall_clock_timeout"]
    assert record and record[0]["runner_stopped"] is False


@pytest.mark.asyncio
async def test_the_idle_watchdog_also_stops_the_runner_inside_the_sandbox() -> None:
    runner = _recording_runner(_HangingProc())
    await runner.run(
        container="sandbox-job-x",
        config={"agent_type": "developer", "timeout_seconds": 30, "idle_grace_seconds": 0.3},
    )
    sent = [c.args[0] for c in runner._docker.run_cmd.await_args_list]
    assert ["docker", "exec", "sandbox-job-x", *_PKILL] in sent, sent


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


# Linux caps ONE argv string at MAX_ARG_STRLEN (32 pages = 128 KiB); past it
# execve fails with E2BIG before docker ever runs.
_MAX_ARG_STRLEN = 131072


@pytest.mark.asyncio
async def test_runner_ships_config_on_stdin_not_argv() -> None:
    """job-df9e93a9ec1f (#304): a pr-followup review prompt carrying the PR
    diff pushed the ``--config <json>`` argv string past MAX_ARG_STRLEN and
    the job died with "[Errno 7] Argument list too long" AFTER the developer
    had pushed. The config must ride stdin, where size is unbounded."""
    proc = _FakeProc(returncode=0, stderr_chunks=[])
    runner = _runner_with_proc(proc)
    config = {"agent_type": "review", "timeout_seconds": 5, "prompt": "d" * (2 * _MAX_ARG_STRLEN)}

    await runner.run(container="c", config=config)

    kwargs = runner._docker.stream_exec.call_args.kwargs
    assert "--config-stdin" in kwargs["command"]
    assert "--config" not in kwargs["command"]
    assert max(len(arg) for arg in kwargs["command"]) < _MAX_ARG_STRLEN
    assert json.loads(kwargs["stdin_data"]) == config


# ---------------------------------------------------------------------------
# #304: stale-image fallback. Prebaked per-repo QA images are `docker commit`s
# of an old sandbox and profile images are built FROM it, so a runner that
# predates --config-stdin can outlive an orchestrator deploy.
# ---------------------------------------------------------------------------

_OLD_RUNNER_USAGE = (
    b"usage: runner.py [-h] --config CONFIG [--session-blob SESSION_BLOB]\n"
    b"runner.py: error: the following arguments are required: --config\n"
)
_FINAL_OK = b'{"type": "final_result", "agent_type": "qa", "is_error": false, "output": "done"}\n'
# An event the runner writes once the agent is under way.
_STARTED = b'{"type": "progress", "message": "agent started"}\n'


class _LinesStdout:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    def __aiter__(self) -> _LinesStdout:
        return self

    async def __anext__(self) -> bytes:
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


def _proc(*, returncode: int, stdout: list[bytes] | None = None, stderr: bytes = b"") -> _FakeProc:
    proc = _FakeProc(returncode=returncode, stderr_chunks=[stderr] if stderr else [])
    proc.stdout = _LinesStdout(stdout or [])  # type: ignore[assignment]
    return proc


def _runner_with_procs(procs: list[_FakeProc]) -> AgentRunner:
    docker = AsyncMock()
    remaining = list(procs)

    async def _stream_exec(**_kwargs: object) -> _FakeProc:
        return remaining.pop(0)

    docker.stream_exec = AsyncMock(side_effect=_stream_exec)
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec", "c", "true"])
    docker.build_inspect_image_cmd = MagicMock(return_value=["docker", "inspect", "c"])
    docker.run_cmd = AsyncMock(return_value="embry0-qa-owner_repo:2026-08-12T22-32\n")
    return AgentRunner(sandbox_manager=AsyncMock(), docker=docker)


@pytest.mark.asyncio
async def test_stale_runner_falls_back_to_argv_once(caplog: pytest.LogCaptureFixture) -> None:
    runner = _runner_with_procs(
        [_proc(returncode=2, stderr=_OLD_RUNNER_USAGE), _proc(returncode=0, stdout=[_FINAL_OK])]
    )
    config = {"agent_type": "qa", "timeout_seconds": 5, "prompt": "small"}

    from structlog.testing import capture_logs

    with capture_logs() as logs:
        out = await runner.run(container="c", config=config)

    assert out.is_error is False and out.output == "done"
    calls = runner._docker.stream_exec.call_args_list
    assert len(calls) == 2
    retry = calls[1].kwargs
    assert retry.get("stdin_data") is None
    assert json.loads(retry["command"][retry["command"].index("--config") + 1]) == config
    stale = [entry for entry in logs if entry["event"] == "sandbox_runner_stale_image"]
    assert stale and stale[0]["log_level"] == "warning"
    assert stale[0]["image"] == "embry0-qa-owner_repo:2026-08-12T22-32"


@pytest.mark.asyncio
async def test_stale_runner_with_oversized_config_fails_with_a_specific_code() -> None:
    from embry0.safety.error_codes import ErrorCode

    runner = _runner_with_procs([_proc(returncode=2, stderr=_OLD_RUNNER_USAGE)])
    config = {"agent_type": "qa", "timeout_seconds": 5, "prompt": "d" * (2 * _MAX_ARG_STRLEN)}

    out = await runner.run(container="c", config=config)

    assert out.is_error is True
    assert out.error_code == ErrorCode.SANDBOX_IMAGE_STALE.value
    assert "embry0-qa-owner_repo:2026-08-12T22-32" in out.error_message
    assert "rebuil" in out.error_message
    assert runner._docker.stream_exec.await_count == 1  # never put an oversized config on argv


@pytest.mark.asyncio
async def test_current_runner_never_falls_back_or_touches_argv() -> None:
    runner = _runner_with_procs([_proc(returncode=0, stdout=[_FINAL_OK])])
    config = {"agent_type": "qa", "timeout_seconds": 5, "prompt": "SECRET-ISH"}

    out = await runner.run(container="c", config=config)

    assert out.is_error is False
    assert runner._docker.stream_exec.await_count == 1
    assert not any("SECRET-ISH" in arg for arg in runner._docker.stream_exec.call_args.kwargs["command"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first",
    [
        # exit 2 + usage text, but the runner had already emitted output: work started.
        {"returncode": 2, "stdout": [b'{"type": "agent_started"}\n'], "stderr": _OLD_RUNNER_USAGE},
        # usage-looking stderr from an agent that ran and exited 1.
        {"returncode": 1, "stderr": _OLD_RUNNER_USAGE},
        # exit 2 for some other reason.
        {"returncode": 2, "stderr": b"python: can't open file\n"},
    ],
)
async def test_fallback_cannot_fire_once_the_runner_did_anything(first: dict) -> None:
    runner = _runner_with_procs([_proc(**first), _proc(returncode=0, stdout=[_FINAL_OK])])

    out = await runner.run(container="c", config={"agent_type": "qa", "timeout_seconds": 5})

    assert out.is_error is True
    assert runner._docker.stream_exec.await_count == 1  # no second execution


@pytest.mark.asyncio
async def test_stdin_handoff_timeout_is_an_agent_error_not_an_exception() -> None:
    from embry0.execution.docker_client import ExecStdinTimeoutError
    from embry0.safety.error_codes import ErrorCode

    docker = AsyncMock()
    docker.stream_exec = AsyncMock(side_effect=ExecStdinTimeoutError("did not read its 9-byte stdin within 60s"))
    runner = AgentRunner(sandbox_manager=AsyncMock(), docker=docker)

    out = await runner.run(container="c", config={"agent_type": "review", "timeout_seconds": 5})

    assert out.is_error is True
    assert out.agent_type == "review"
    assert out.error_code == ErrorCode.SANDBOX_EXEC_STALLED.value
    assert "did not read" in out.error_message


# --- #281: errored runs carry a code and a redacted stderr tail -------------


def _final_error(message: str) -> bytes:
    return (
        json.dumps(
            {"type": "final_result", "agent_type": "developer", "is_error": True, "error_message": message}
        ).encode()
        + b"\n"
    )


@pytest.mark.asyncio
async def test_cli_crash_keeps_its_stderr_tail_redacted() -> None:
    """The 27 developer exit-1 runs: the SDK says only "check stderr", and the
    stderr that explained it was dropped whenever a final_result arrived."""
    token = "ghp_" + "a1B2" * 9
    stderr = f"Error: ENOSPC: no space left on device\nAuthorization: Bearer {token}\n".encode()
    proc = _proc(
        returncode=1,
        stdout=[
            _final_error(
                "Command failed with exit code 1 (exit code: 1)\nError output: Check stderr output for details"
            )
        ],
        stderr=stderr,
    )
    out = await _runner_with_procs([proc]).run(container="c", config={"agent_type": "developer"})

    assert out.is_error is True
    assert out.error_code == "ERR_AGENT_PROCESS_FAILED"
    assert "ENOSPC" in out.stderr_tail
    assert token not in out.stderr_tail


@pytest.mark.asyncio
async def test_typed_provider_code_survives_a_message_without_the_needle() -> None:
    """The executor's error_code is the only signal when the text has no needle."""
    line = (
        json.dumps(
            {
                "type": "final_result",
                "agent_type": "developer",
                "is_error": True,
                "error_message": "nope",
                "error_code": "ERR_PROVIDER_RATE_LIMITED",
            }
        ).encode()
        + b"\n"
    )
    out = await _runner_with_procs([_proc(returncode=1, stdout=[line])]).run(
        container="c", config={"agent_type": "developer"}
    )
    assert out.error_code == "ERR_PROVIDER_RATE_LIMITED", (
        "a typed rate-limit code must survive the runner; the message 'nope' has no needle"
    )


@pytest.mark.asyncio
async def test_turn_cap_death_gets_its_own_code() -> None:
    proc = _proc(
        returncode=1,
        stdout=[_final_error("Claude Code returned an error result: Reached maximum number of turns (25)")],
    )
    out = await _runner_with_procs([proc]).run(container="c", config={"agent_type": "review"})
    assert out.error_code == "ERR_AGENT_MAX_TURNS"


@pytest.mark.asyncio
async def test_no_final_result_and_idle_kill_are_coded() -> None:
    # The agent started (one event on stdout), then died without a result.
    out = await _runner_with_procs([_proc(returncode=1, stdout=[_STARTED], stderr=b"Traceback: boom\n")]).run(
        container="c", config={"agent_type": "qa"}
    )
    assert out.error_code == "ERR_AGENT_NO_RESULT"
    assert "boom" in out.stderr_tail

    idle = await _runner_with_proc(_HangingProc()).run(
        container="c", config={"agent_type": "qa", "timeout_seconds": 30, "idle_grace_seconds": 0.3}
    )
    assert idle.error_code == "ERR_AGENT_IDLE"


@pytest.mark.asyncio
async def test_a_successful_run_stores_no_stderr() -> None:
    proc = _proc(returncode=0, stdout=[_FINAL_OK], stderr=b"DeprecationWarning: noisy but harmless\n")
    out = await _runner_with_procs([proc]).run(container="c", config={"agent_type": "qa"})
    assert out.is_error is False
    assert out.stderr_tail == ""
    assert out.error_code is None


@pytest.mark.asyncio
async def test_stderr_tail_is_size_capped() -> None:
    from embry0.safety.redaction import STDERR_TAIL_MAX_BYTES

    noise = b"".join(b"warning line %06d\n" % i for i in range(40_000))  # ~760 KiB
    proc = _proc(
        returncode=1, stdout=[_final_error("Command failed with exit code 1")], stderr=noise + b"the real cause\n"
    )
    out = await _runner_with_procs([proc]).run(container="c", config={"agent_type": "developer"})
    assert len(out.stderr_tail.encode()) <= STDERR_TAIL_MAX_BYTES
    assert out.stderr_tail.endswith("the real cause")


# --- #281 review: the data-exposure guarantees, each with its own test ------


@pytest.mark.asyncio
async def test_no_final_result_message_is_redacted_before_it_is_stored() -> None:
    """The trailing stderr is pasted into error_message, which reaches the
    job row, the dashboard and the logs."""
    token = "ghp_" + "c3D4" * 9
    proc = _proc(returncode=1, stderr=f"fatal: could not read Username for {token}\n".encode())
    out = await _runner_with_procs([proc]).run(container="c", config={"agent_type": "qa"})

    assert token not in out.error_message
    assert "could not read Username" in out.error_message
    assert "***REDACTED***" in out.error_message


@pytest.mark.asyncio
async def test_the_subprocess_failure_log_line_is_redacted() -> None:
    """That line ships to Loki, where it outlives the job row."""
    token = "ghp_" + "e5F6" * 9
    proc = _proc(
        returncode=1,
        stdout=[_final_error("Command failed with exit code 1")],
        stderr=f"Authorization: Bearer {token}\n".encode(),
    )
    with structlog.testing.capture_logs() as logs:
        await _runner_with_procs([proc]).run(container="c", config={"agent_type": "developer"})

    line = next(e for e in logs if e["event"] == "agent_runner_subprocess_failed")
    assert token not in line["stderr"]
    assert "***REDACTED***" in line["stderr"]


@pytest.mark.asyncio
async def test_subprocess_failure_log_includes_result_payload_when_stderr_is_empty() -> None:
    """An is_error runner exits 1 with nothing on stderr. The final_result already
    carried the diagnosis; the failure log must too, redacted."""
    token = "ghp_" + "a1B2" * 9
    payload = {
        "type": "final_result",
        "agent_type": "triage",
        "is_error": True,
        "error_message": f"API Error: 529 overloaded {token}",
        "error_code": "ERR_UPSTREAM_HTTP",
        "output": "no triage decision",
        "diagnostic": f"error_code=none error_message=API Error: 529 overloaded {token}",
    }
    proc = _proc(returncode=1, stdout=[json.dumps(payload).encode() + b"\n"], stderr=b"")
    with structlog.testing.capture_logs() as logs:
        await _runner_with_procs([proc]).run(container="c", config={"agent_type": "triage"})

    line = next(e for e in logs if e["event"] == "agent_runner_subprocess_failed")
    assert line["exit_code"] == 1
    assert line["stderr"] == ""
    assert line["error_code"] == "ERR_UPSTREAM_HTTP"
    assert "529" in line["error_message"]
    assert token not in line["error_message"]
    assert "***REDACTED***" in line["error_message"]
    assert line["result"] == "no triage decision"
    assert token not in line["diagnostic"]
    assert "529" in line["diagnostic"]


@pytest.mark.asyncio
async def test_a_chatty_agent_cannot_grow_the_stderr_buffer_without_bound() -> None:
    """Only the tail is ever used, and a wedged MCP server can write for
    hours. The buffer keeps the newest bytes and drops the rest as it goes."""
    from embry0.execution.agent_runner import _STDERR_BUFFER_MAX_BYTES

    chunk = b"noise noise noise\n" * 256  # ~4.5 KiB, as the real read size
    chunks = [chunk] * 200  # ~900 KiB total, 3.5x the cap
    proc = _FakeProc(returncode=1, stderr_chunks=[*chunks, b"the real cause\n"])
    proc.stdout = _LinesStdout([])  # type: ignore[assignment]

    runner = _runner_with_procs([proc])
    _out, exit_facts = await runner._exec_runner("c", ["runner"], None, {"agent_type": "qa"}, None)

    retained = len(exit_facts.stderr.encode())
    assert retained <= _STDERR_BUFFER_MAX_BYTES + len(chunk), retained
    assert retained < len(b"".join(chunks))
    assert exit_facts.stderr.endswith("the real cause\n")


@pytest.mark.asyncio
async def test_stderr_mentioning_a_turn_cap_does_not_hijack_the_diagnosis() -> None:
    """The no-final-result message embeds the agent's own stderr. Classified
    as text, a traceback that merely MENTIONS the turn cap would be read as
    one — so that site states its code instead."""
    stderr = b"Traceback (most recent call last):\n  RuntimeError: Reached maximum number of turns (25)\n"
    proc = _proc(returncode=1, stdout=[_STARTED], stderr=stderr)
    out = await _runner_with_procs([proc]).run(container="c", config={"agent_type": "review"})

    assert out.error_code == "ERR_AGENT_NO_RESULT"
    assert "Reached maximum number of turns" in out.error_message


# --- #354: a crash before the runner starts the agent -------------------------

_PARTIAL_IMAGE_STDERR = (
    b"Traceback (most recent call last):\n"
    b'  File "/app/embry0/sandbox/runner.py", line 22, in <module>\n'
    b"ImportError: cannot import name 'classify_agent_error' from 'embry0.safety.error_codes'\n"
)


@pytest.mark.asyncio
async def test_a_crash_before_the_first_event_is_a_start_failure_not_no_result() -> None:
    proc = _proc(returncode=1, stderr=_PARTIAL_IMAGE_STDERR)
    out = await _runner_with_procs([proc]).run(container="c", config={"agent_type": "developer"})

    assert out.error_code == "ERR_SANDBOX_AGENT_START_FAILED", (
        f"a runner that died importing its modules was coded {out.error_code!r}"
    )
    assert "could not start the agent" in out.error_message
    assert "classify_agent_error" in out.error_message
    assert "classify_agent_error" in out.stderr_tail, "the stored stderr tail must carry the import failure"


@pytest.mark.asyncio
async def test_a_start_crash_stderr_is_redacted() -> None:
    token = "ghp_" + "g7H8" * 9
    stderr = _PARTIAL_IMAGE_STDERR + f"ModuleNotFoundError: GITHUB_TOKEN={token}\n".encode()
    out = await _runner_with_procs([_proc(returncode=1, stderr=stderr)]).run(
        container="c", config={"agent_type": "developer"}
    )
    assert out.error_code == "ERR_SANDBOX_AGENT_START_FAILED"
    assert token not in out.error_message
    assert token not in out.stderr_tail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr"),
    [
        (1, [_STARTED], _PARTIAL_IMAGE_STDERR),  # the agent had started: it ran and returned nothing
        (1, [], b"killed\n"),  # no Python failure on stderr: nothing says the start failed
        (0, [], _PARTIAL_IMAGE_STDERR),  # a clean exit is not a crash
    ],
)
async def test_only_a_pre_start_python_crash_is_a_start_failure(returncode, stdout, stderr) -> None:
    out = await _runner_with_procs([_proc(returncode=returncode, stdout=stdout, stderr=stderr)]).run(
        container="c", config={"agent_type": "developer"}
    )
    assert out.error_code == ErrorCode.AGENT_NO_RESULT.value


@pytest.mark.asyncio
async def test_an_image_with_inconsistent_baked_modules_is_reported_as_a_start_failure(tmp_path) -> None:
    """The shape PR #352's review reproduced: a partial image refresh that
    pairs a new runner with an older embry0/safety/. A real interpreter runs
    the runner module from such a tree and dies importing it."""
    import asyncio
    import os
    import sys

    pkg = tmp_path / "embry0"
    for sub in ("", "sandbox", "safety"):
        (pkg / sub).mkdir(exist_ok=True)
        (pkg / sub / "__init__.py").write_text("")
    (pkg / "safety" / "error_codes.py").write_text("class ErrorCode:\n    pass\n")  # the older module
    (pkg / "sandbox" / "runner.py").write_text(
        "from embry0.safety.error_codes import classify_agent_error\n"  # the newer runner
    )

    async def _stream_exec(*, command, stdin_data=None, **_kwargs):
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            *command[1:],
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(tmp_path)},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdin is not None
        proc.stdin.write(stdin_data or b"")
        proc.stdin.close()
        return proc

    runner = _runner_with_procs([])
    runner._docker.stream_exec = AsyncMock(side_effect=_stream_exec)

    out = await runner.run(container="c", config={"agent_type": "developer"})

    assert out.error_code == "ERR_SANDBOX_AGENT_START_FAILED", (
        f"an image whose runner cannot import its safety module was coded {out.error_code!r}: {out.error_message}"
    )
    assert "cannot import name 'classify_agent_error'" in out.stderr_tail
