"""An agent run's result is taken only when it is its runner's own (spec 2026-11-23, an agent run's own result).

The orchestrator hands the runner a per-run mark, a nonce, on stdin with the
config, never on argv or in the environment, which other processes of the
sandbox's user can read. The runner writes it back on its one
``final_result`` line. A result without the mark, or a second result, fails
the run: whichever line is the runner's, the other is not, and neither is
trusted. An image whose runner predates the mark, or predates the runner's
execute-only interpreter, runs once more without it, for one release, and is
counted as a stale image.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing

from embry0.execution import agent_runner
from embry0.execution.agent_runner import AgentRunner
from tests.fixtures.sandbox_runner import RESULT_NONCE_KEY, as_the_runner

_FINAL = {"type": "final_result", "agent_type": "developer", "is_error": False, "output": "done", "cost_usd": 0.01}
_ANOTHER = {"type": "final_result", "agent_type": "developer", "is_error": False, "output": "another", "cost_usd": 0.0}
_STARTED = {"type": "agent_started", "model": "custom/m", "max_turns": 5}
_REFUSED = "ERR_SANDBOX_RESULT_REFUSED"
_INTERPRETER = "/usr/local/bin/embry0-runner-python"
#: What argparse prints when a runner that takes its config on stdin is handed a flag it does not know.
_UNMARKED_USAGE = (
    b"usage: runner.py [-h] (--config CONFIG | --config-stdin) [--session-blob SESSION_BLOB]\n"
    b"                 [--session-id SESSION_ID]\n"
    b"runner.py: error: unrecognized arguments: --result-nonce\n"
)
#: What docker exec prints when the program it is to start is not in the image.
_NO_INTERPRETER = (
    b"OCI runtime exec failed: exec failed: unable to start container process: exec: "
    b'"/usr/local/bin/embry0-runner-python": stat /usr/local/bin/embry0-runner-python: no such file or directory\n'
)


class _Lines:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    def __aiter__(self) -> _Lines:
        return self

    async def __anext__(self) -> bytes:
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _Stderr:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self, _n: int) -> bytes:
        data, self._data = self._data, b""
        return data


class _Proc:
    def __init__(self, lines: list[bytes], *, returncode: int = 0, stderr: bytes = b"") -> None:
        self.stdout = _Lines(lines)
        self.stderr = _Stderr(stderr)
        self.returncode = returncode

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:  # pragma: no cover - only a timeout kills
        pass


def _line(event: dict[str, Any]) -> bytes:
    return json.dumps(event).encode() + b"\n"


def _proc(
    events: list[dict[str, Any]],
    *,
    returncode: int = 0,
    stderr: bytes = b"",
    mark: bool = True,
    written_by_another: tuple[int, dict[str, Any]] | None = None,
) -> Any:
    """One exec's process: ``events`` on stdout, its final results marked as the runner marks them unless
    ``mark`` is False; ``written_by_another`` is a line something else wrote among them, at that index."""
    proc = _Proc([_line(event) for event in events], returncode=returncode, stderr=stderr)
    proc.mark = mark
    proc.written_by_another = written_by_another
    return proc


def _runner(procs: list[Any]) -> tuple[AgentRunner, list[str]]:
    """A runner whose execs return ``procs`` in turn; with the outcomes it counted as stale images."""
    remaining = list(procs)
    stale: list[str] = []

    async def _stream_exec(*, stdin_data: bytes | None = None, **_kwargs: Any) -> Any:
        proc = remaining.pop(0)
        if proc.mark:
            as_the_runner(proc, stdin_data)
        if proc.written_by_another is not None:
            index, event = proc.written_by_another
            proc.stdout._lines.insert(index, _line(event))
        return proc

    docker = AsyncMock()
    docker.stream_exec = AsyncMock(side_effect=_stream_exec)
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec", "c", "true"])
    docker.build_inspect_image_cmd = MagicMock(return_value=["docker", "inspect", "c"])
    docker.run_cmd = AsyncMock(return_value="acme-sandbox:old\n")
    return AgentRunner(sandbox_manager=AsyncMock(), docker=docker, on_stale_image=stale.append), stale


async def _run(procs: list[Any]) -> tuple[Any, list[dict[str, Any]], AgentRunner, list[str]]:
    runner, stale = _runner(procs)
    with structlog.testing.capture_logs() as logs:
        output = await runner.run(container="c", config={"agent_type": "developer", "prompt": "fix it"})
    return output, logs, runner, stale


def _calls(runner: AgentRunner) -> list[dict[str, Any]]:
    return [call.kwargs for call in runner._docker.stream_exec.call_args_list]  # type: ignore[attr-defined]


def _nonce(call: dict[str, Any]) -> Any:
    return json.loads(call["stdin_data"] or b"{}").get(RESULT_NONCE_KEY)


# --- the mark ------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_run_s_mark_rides_stdin_and_nowhere_else() -> None:
    _, _, first, _ = await _run([_proc([_STARTED, _FINAL])])
    _, _, second, _ = await _run([_proc([_STARTED, _FINAL])])
    call = _calls(first)[0]
    nonce = _nonce(call)

    assert isinstance(nonce, str) and len(nonce) >= 32, f"the config on stdin carries no per-run mark: {nonce!r}"
    assert "--result-nonce" in call["command"], "the runner is not told its config carries a mark"
    assert not any(nonce in arg for arg in call["command"]), "the mark is on the runner's command line"
    assert not any(nonce in str(value) for value in (call.get("env") or {}).values()), "the mark is in its env"
    assert _nonce(_calls(second)[0]) != nonce, "two runs were handed the same mark"


@pytest.mark.asyncio
async def test_a_result_carrying_the_run_s_mark_is_taken_and_the_mark_goes_no_further() -> None:
    output, logs, runner, _ = await _run([_proc([_STARTED, _FINAL], returncode=0)])
    nonce = _nonce(_calls(runner)[0])

    assert isinstance(nonce, str), f"the run was handed no mark: {nonce!r}"
    assert output.is_error is False and output.output == "done", f"the run's own result was refused: {output}"
    assert nonce not in repr(output) and nonce not in repr(logs), "the run's mark left the read loop"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "written",
    [
        pytest.param({}, id="no-mark"),
        pytest.param({"nonce": "zz-not-the-run-s-mark"}, id="another-mark"),
        pytest.param({"nonce": None}, id="null"),
        pytest.param({"nonce": 12345}, id="number"),
        pytest.param({"nonce": ["zz"]}, id="list"),
    ],
)
async def test_a_result_without_the_run_s_mark_fails_the_run(written: dict[str, Any]) -> None:
    output, logs, _, _ = await _run([_proc([_STARTED, {**_ANOTHER, **written}], mark=False)])

    assert output.is_error is True and output.error_code == _REFUSED, (
        f"a result without the run's mark was taken: {output}"
    )
    assert "another" not in output.output, "the unmarked result's output reached the run's output"
    refused = [e for e in logs if e["event"] == "agent_runner_result_refused"]
    assert refused and refused[0]["log_level"] == "warning", f"the refusal was not logged: {logs}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "order",
    [
        pytest.param("unmarked-first", id="another-line-first"),
        pytest.param("unmarked-last", id="another-line-last"),
        pytest.param("marked-twice", id="the-mark-twice"),
    ],
)
async def test_a_second_final_result_fails_the_run_whichever_it_is(order: str) -> None:
    if order == "marked-twice":
        proc = _proc([_STARTED, _FINAL, _FINAL])
    else:
        proc = _proc([_STARTED, _FINAL], written_by_another=(1 if order == "unmarked-first" else 2, _ANOTHER))

    output, logs, _, _ = await _run([proc])

    assert output.is_error is True and output.error_code == _REFUSED, f"two final results, one taken: {output}"
    assert "another" not in output.output and output.output != "done", "either result reached the run's output"
    refused = [e for e in logs if e["event"] == "agent_runner_result_refused"]
    assert refused and refused[0]["results"] == 2, f"the refusal does not count the results: {refused}"


# --- an image whose runner predates the mark ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_image_whose_runner_predates_the_mark_runs_once_more_without_it() -> None:
    output, logs, runner, stale = await _run(
        [_proc([], returncode=2, stderr=_UNMARKED_USAGE), _proc([_STARTED, _FINAL], mark=False)]
    )
    calls = _calls(runner)

    assert len(calls) == 2, f"the image's runner refused the mark and was not run again: {len(calls)} exec(s)"
    assert "--result-nonce" not in calls[1]["command"] and _nonce(calls[1]) is None, "the retry still marks"
    assert output.is_error is False and output.output == "done", f"its unmarked result was refused: {output}"
    assert stale == ["result_unmarked"], f"the stale image was not counted: {stale}"
    warned = [e for e in logs if e["event"] == "sandbox_runner_stale_image"]
    assert warned and warned[0]["outcome"] == "result_unmarked" and warned[0]["image"] == "acme-sandbox:old"


@pytest.mark.asyncio
async def test_on_an_image_without_the_mark_a_second_final_result_still_fails_the_run() -> None:
    output, _, _, _ = await _run(
        [_proc([], returncode=2, stderr=_UNMARKED_USAGE), _proc([_STARTED, _ANOTHER, _FINAL], mark=False)]
    )

    assert output.is_error is True and output.error_code == _REFUSED, f"two results on an unmarked run: {output}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first",
    [
        pytest.param({"events": [_STARTED], "returncode": 2, "stderr": _UNMARKED_USAGE}, id="it-wrote-a-line"),
        pytest.param({"events": [], "returncode": 1, "stderr": _UNMARKED_USAGE}, id="not-a-usage-exit"),
        pytest.param({"events": [], "returncode": 2, "stderr": b"error: unrecognized arguments: --x\n"}, id="other"),
    ],
)
async def test_the_unmarked_retry_cannot_fire_once_the_runner_did_anything(first: dict[str, Any]) -> None:
    _, _, runner, stale = await _run([_proc(**first), _proc([_STARTED, _FINAL])])

    assert len(_calls(runner)) == 1 and stale == [], "the runner was run again after it had run or failed otherwise"


# --- the runner's interpreter ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_runner_runs_on_its_execute_only_interpreter() -> None:
    _, _, runner, _ = await _run([_proc([_STARTED, _FINAL])])
    command = _calls(runner)[0]["command"]

    assert command[:5] == [_INTERPRETER, "-P", "-s", "-m", "embry0.sandbox.runner"], (
        f"the runner is not started on its execute-only interpreter: {command[:5]}"
    )


@pytest.mark.asyncio
async def test_an_image_without_the_runner_s_interpreter_runs_it_on_its_own_python() -> None:
    output, logs, runner, stale = await _run(
        [_proc([], returncode=127, stderr=_NO_INTERPRETER), _proc([_STARTED, _FINAL])]
    )
    calls = _calls(runner)

    assert len(calls) == 2, f"an image without the interpreter was not run again: {len(calls)} exec(s)"
    assert calls[1]["command"][:5] == ["python", "-P", "-s", "-m", "embry0.sandbox.runner"], calls[1]["command"]
    assert "--result-nonce" in calls[1]["command"] and _nonce(calls[1]) == _nonce(calls[0]), "the retry is unmarked"
    assert output.is_error is False and output.output == "done", f"its marked result was refused: {output}"
    assert stale == ["interpreter_missing"], f"the stale image was not counted: {stale}"
    warned = [e for e in logs if e["event"] == "sandbox_runner_stale_image"]
    assert warned and warned[0]["outcome"] == "interpreter_missing"


@pytest.mark.asyncio
async def test_an_image_without_the_interpreter_or_the_mark_runs_on_its_python_unmarked() -> None:
    output, _, runner, stale = await _run(
        [
            _proc([], returncode=127, stderr=_NO_INTERPRETER),
            _proc([], returncode=2, stderr=_UNMARKED_USAGE),
            _proc([_STARTED, _FINAL], mark=False),
        ]
    )
    last = _calls(runner)[-1]

    assert last["command"][0] == "python" and "--result-nonce" not in last["command"], last["command"]
    assert output.output == "done" and stale == ["interpreter_missing", "result_unmarked"], (output, stale)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first",
    [
        pytest.param({"events": [_STARTED], "returncode": 127, "stderr": _NO_INTERPRETER}, id="it-wrote-a-line"),
        pytest.param({"events": [], "returncode": 127, "stderr": b"sh: 1: claude: not found\n"}, id="other-program"),
        pytest.param({"events": [], "returncode": 126, "stderr": _NO_INTERPRETER}, id="not-exec-not-found"),
    ],
)
async def test_the_interpreter_retry_cannot_fire_otherwise(first: dict[str, Any]) -> None:
    _, _, runner, stale = await _run([_proc(**first), _proc([_STARTED, _FINAL])])

    assert len(_calls(runner)) == 1 and stale == [], "the runner was run again on the image's python"


@pytest.mark.asyncio
@pytest.mark.parametrize(("reported", "warned"), [(True, True), (False, False), (None, False)])
async def test_a_runner_that_started_open_says_so(reported: bool | None, warned: bool) -> None:
    final = {**_FINAL, **({"open_at_start": reported} if reported is not None else {})}
    output, logs, _, _ = await _run([_proc([_STARTED, final])])

    said = [e for e in logs if e["event"] == "sandbox_runner_open_at_start"]
    assert bool(said) is warned, f"open_at_start={reported!r}: {said}"
    assert output.output == "done", output


def test_the_orchestrator_and_the_runner_name_the_mark_alike() -> None:
    from embry0.sandbox import runner

    assert isinstance(getattr(runner, "RESULT_NONCE_KEY", None), str), "the runner reads no mark from its config"
    assert getattr(agent_runner, "_RESULT_NONCE_KEY", None) == getattr(runner, "RESULT_NONCE_KEY", None), (
        "the orchestrator puts the mark under one key and the runner reads another"
    )
    assert getattr(runner, "RESULT_NONCE_KEY", None) == RESULT_NONCE_KEY, "the test fixture marks another key"
    assert getattr(agent_runner, "RUNNER_INTERPRETER", None) == _INTERPRETER
