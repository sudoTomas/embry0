"""The sandbox side of the gate (#299) — driven through a fake docker client."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from embry0.execution.docker_client import OutputTooLargeError
from embry0.workflows.verification.config import VerificationConfig
from embry0.workflows.verification.runner import BASE_DIR, HEAD_DIR, run_gate, select_targets

HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
CLEAN_SWEEP = "EMBRY0_SWEEP stopped=0 passes=0 survivors=0 unread=0 pids=\n"
DAEMON_GONE = "Error response from daemon: No such container: c1\n"
# How long each step takes on the fake clock: only the run's exec counts as the run.
RUN_SECONDS, STEP_SECONDS = 1.234, 0.5


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """The runner's clock, which the fake sandbox moves on as each step takes time."""
    from embry0.workflows.verification import runner

    fake = _Clock()
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=fake.monotonic))
    FakeDocker.clock = fake
    return fake


class FakeDocker:
    """A dev sandbox, answering each exec the gate makes as docker would.

    ``results`` maps (dir, command) to a list of (exit_code, log) consumed one
    per attempt; the last entry repeats. A run's exec exits with that status,
    writes the log and prints nothing (``printed`` gives a run what its exec
    prints itself, as (stdout, stderr)); the log is read back by a ``tail``
    exec of its own. Codes below zero are docker failing: -1 a daemon error,
    -2 an exec that never returns, -3 one that prints past its cap.
    ``sweeps`` answers the process sweeps in turn, as (code, stdout, stderr);
    once used up, every sweep reports a clean sandbox.

    ``run_cmd`` raises on a non-zero exit and strips, as DockerClient's does;
    ``run_cmd_status`` returns the status and both outputs as printed.
    """

    clock: _Clock

    def __init__(
        self,
        results: dict[tuple[str, str], list[tuple[int, str]]],
        *,
        prepare_fails: bool = False,
        prepared: str | None = None,
        printed: dict[tuple[str, str], tuple[str, str]] | None = None,
        sweeps: list[tuple[int, str, str]] | None = None,
        log_read_fails: bool = False,
        log_stderr: str = "",
    ) -> None:
        self.results = {k: list(v) for k, v in results.items()}
        self.prepare_fails = prepare_fails
        self.prepared = (
            prepared if prepared is not None else f"EMBRY0_VERIFY_HEAD={HEAD_SHA}\nEMBRY0_VERIFY_BASE={BASE_SHA}"
        )
        self.printed = printed or {}
        self.sweeps = list(sweeps or [])
        self.log_read_fails = log_read_fails
        self.log_stderr = log_stderr
        self.runs: list[tuple[str, str]] = []
        self.timeouts: list[int] = []
        self.caps: list[int | None] = []
        self.log_caps: list[int | None] = []
        self.steps: list[str] = []
        self.sweep_users: list[str | None] = []
        self.logs: dict[str, str] = {}
        self.cleaned = False

    def build_exec_cmd(self, container: str, command: list[str], workdir: Any = None, env: Any = None, **kw: Any):
        return {"container": container, "command": command, "env": env or {}, "user": kw.get("user")}

    def _answer(self, cmd: dict[str, Any], max_bytes: int | None) -> tuple[int, str, str]:
        env, command = cmd["env"], cmd["command"]
        script = command[-1]
        if "EMBRY0_VERIFY_CMD" in env:
            self.steps.append("run")
            key = (env["EMBRY0_VERIFY_DIR"], env["EMBRY0_VERIFY_CMD"])
            self.runs.append(key)
            self.timeouts.append(int(env["EMBRY0_VERIFY_TIMEOUT"]))
            self.caps.append(max_bytes)
            queue = self.results[key]
            code, log = queue.pop(0) if len(queue) > 1 else queue[0]
            self.clock.now += RUN_SECONDS
            if code == -3:
                raise OutputTooLargeError(f"command output passed its {max_bytes}-byte cap; the command was stopped")
            if code == -2:
                raise TimeoutError("docker command timed out")
            if code == -1:
                return 1, "", DAEMON_GONE
            self.logs[env["EMBRY0_VERIFY_LOG"]] = log
            out, err = self.printed.get(key, ("", ""))
            return code, out, err
        if command[0] == "tail":
            self.steps.append("log")
            self.log_caps.append(max_bytes)
            self.clock.now += STEP_SECONDS
            name = script.rsplit("/", 1)[-1]
            if self.log_read_fails or name not in self.logs:
                return 1, "", f"tail: cannot open '{script}' for reading: No such file or directory\n"
            return 0, self.logs[name], self.log_stderr
        if "EMBRY0_SWEEP" in script:
            self.steps.append("sweep")
            self.sweep_users.append(cmd["user"])
            self.clock.now += STEP_SECONDS
            return self.sweeps.pop(0) if self.sweeps else (0, CLEAN_SWEEP, "")
        if "worktree add" in script:
            self.steps.append("prepare")
            if self.prepare_fails:
                return 128, "", "fatal: not a git repository\n"
            return 0, self.prepared, ""
        if "worktree remove" in script:
            self.steps.append("cleanup")
            self.cleaned = True
            return 0, "", ""
        raise AssertionError(f"unexpected exec: {script[:80]}")

    async def run_cmd(self, cmd: dict[str, Any], timeout: int = 60, *, max_bytes: int | None = None) -> str:
        code, out, err = self._answer(cmd, max_bytes)
        if code != 0:
            raise RuntimeError(f"Docker command failed: {err.strip() or out.strip()[-2000:] or f'exit code {code}'}")
        return out.strip()

    async def run_cmd_status(
        self, cmd: dict[str, Any], timeout: int = 60, *, max_bytes: int | None = None
    ) -> tuple[int, str, str]:
        return self._answer(cmd, max_bytes)


def _config(**overrides: Any) -> VerificationConfig:
    raw: dict[str, Any] = {
        "test_targets": [
            {"name": "jvm", "paths": ["platform/**"], "command": "cd platform && ./mvnw -q test"},
            {"name": "web", "paths": ["frontend/**"], "command": "cd frontend && npm test"},
        ],
        **overrides,
    }
    return VerificationConfig.model_validate(raw)


JVM = "cd platform && ./mvnw -q test"


async def _gate(docker: FakeDocker, config: VerificationConfig, changed: list[str]):
    return await run_gate(docker=docker, container_id="c1", config=config, changed_files=changed, base_ref="main")


def test_targets_are_selected_by_changed_paths() -> None:
    targets = _config().test_targets
    assert [t.name for t in select_targets(targets, ["platform/api/Foo.java"])] == ["jvm"]
    assert [t.name for t in select_targets(targets, ["docs/x.md"])] == []


def test_an_unknown_diff_selects_every_target() -> None:
    assert [t.name for t in select_targets(_config().test_targets, [])] == ["jvm", "web"]


def test_a_target_without_paths_always_runs() -> None:
    config = VerificationConfig.model_validate({"test_targets": [{"name": "all", "command": "make test"}]})
    assert [t.name for t in select_targets(config.test_targets, ["docs/x.md"])] == ["all"]


@pytest.mark.asyncio
async def test_green_head_and_base_pass_and_both_runs_are_kept() -> None:
    docker = FakeDocker({(HEAD_DIR, JVM): [(0, "ok")], (BASE_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(), ["platform/api/Foo.java"])
    assert report.verdict == "passed"
    assert (report.base_sha, report.head_sha, report.base_ref) == (BASE_SHA, HEAD_SHA, "main")
    assert [(r.side, r.outcome, r.duration_ms) for r in report.runs] == [
        ("head", "passed", 1234),
        ("base", "passed", 1234),
    ]
    assert docker.cleaned is True


@pytest.mark.asyncio
async def test_base_run_can_be_skipped_when_head_is_green() -> None:
    docker = FakeDocker({(HEAD_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(base_run="on_head_failure"), ["platform/api/Foo.java"])
    assert report.verdict == "passed"
    assert docker.runs == [(HEAD_DIR, JVM)]


@pytest.mark.asyncio
async def test_a_new_failure_is_rerun_then_blocks() -> None:
    red = (1, "[ERROR]   FooTest.bar:12 expected 1\n[ERROR] Tests run: 3, Failures: 1, Errors: 0, Skipped: 0")
    docker = FakeDocker({(HEAD_DIR, JVM): [red], (BASE_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(), ["platform/api/Foo.java"])
    assert report.verdict == "new_failures"
    assert report.targets[0].new_failures == ["FooTest.bar"]
    assert docker.runs == [(HEAD_DIR, JVM), (BASE_DIR, JVM), (HEAD_DIR, JVM)]
    assert [r.attempt for r in report.runs_for("head")] == [1, 2]


@pytest.mark.asyncio
async def test_a_flapping_baseline_is_not_rerun_and_does_not_block() -> None:
    red = (1, "FAILED tests/test_flap.py::test_flap - boom")
    docker = FakeDocker({(HEAD_DIR, JVM): [red], (BASE_DIR, JVM): [red]})
    report = await _gate(docker, _config(), ["platform/api/Foo.java"])
    assert report.verdict == "unstable"
    assert report.targets[0].preexisting == ["tests/test_flap.py::test_flap"]
    assert docker.runs == [(HEAD_DIR, JVM), (BASE_DIR, JVM)]


@pytest.mark.asyncio
async def test_a_passing_rerun_records_a_flake() -> None:
    docker = FakeDocker({(HEAD_DIR, JVM): [(1, "boom"), (0, "ok")], (BASE_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(), ["platform/api/Foo.java"])
    assert report.verdict == "unstable"
    assert report.targets[0].status == "flaky"


@pytest.mark.asyncio
async def test_timeout_exit_code_is_a_timed_out_run() -> None:
    docker = FakeDocker({(HEAD_DIR, JVM): [(124, "")], (BASE_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    assert report.runs_for("head")[0].outcome == "timed_out"
    assert report.verdict == "new_failures"
    assert docker.timeouts[0] == 1200


@pytest.mark.asyncio
async def test_an_exec_that_outlives_the_orchestrators_limit_is_errored_not_a_red_head() -> None:
    """Past the in-sandbox timeout and its grace the docker client itself has not returned: that says
    nothing about the tests, so it is not a red head. Only 124/137 from the exec is a timed-out run."""
    docker = FakeDocker({(HEAD_DIR, JVM): [(-2, "")], (BASE_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    head = report.runs_for("head")[0]
    assert head.outcome == "errored", f"a wedged docker exec is not a timed-out test run: {head.outcome}"
    assert head.output_tail == "the run's exec did not return within the orchestrator's limit", head.output_tail
    assert report.verdict == "error"


@pytest.mark.asyncio
async def test_a_docker_failure_is_an_error_verdict_not_a_pass() -> None:
    docker = FakeDocker({(HEAD_DIR, JVM): [(-1, "")], (BASE_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(), ["platform/api/Foo.java"])
    assert report.verdict == "error"
    assert report.blocking is False


@pytest.mark.asyncio
async def test_a_run_that_prints_past_its_cap_is_an_error_verdict_that_says_so() -> None:
    """A run's exec prints nothing; one that prints past its cap is stopped, recorded as not run, never a pass.
    The log is read in its own exec, with a cap just past its tail."""
    from embry0.execution.docker_client import DEFAULT_MAX_OUTPUT_BYTES
    from embry0.workflows.verification import runner

    cap = getattr(runner, "RUN_OUTPUT_MAX_BYTES", None)
    assert cap is not None and cap > runner.PARSE_TAIL_BYTES, "a log must be read with a cap just past its tail"
    docker = FakeDocker({(HEAD_DIR, JVM): [(-3, "")], (BASE_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(), ["platform/api/Foo.java"])
    head = report.runs_for("head")[0]
    assert head.outcome == "errored"
    assert f"printed more than {DEFAULT_MAX_OUTPUT_BYTES} bytes" in head.output_tail, (
        f"the note must say why: {head.output_tail!r}"
    )
    assert report.verdict == "error"
    assert docker.caps == [None], f"a run's exec prints nothing, so it takes the default cap: {docker.caps}"

    green = FakeDocker({(HEAD_DIR, JVM): [(0, "ok")], (BASE_DIR, JVM): [(0, "ok")]})
    await _gate(green, _config(), ["platform/api/Foo.java"])
    assert green.log_caps == [cap, cap], f"every log is read with RUN_OUTPUT_MAX_BYTES: {green.log_caps}"


@pytest.mark.asyncio
async def test_a_workspace_that_cannot_be_prepared_is_an_error_verdict() -> None:
    report = await _gate(FakeDocker({}, prepare_fails=True), _config(), ["platform/api/Foo.java"])
    assert report.verdict == "error"
    assert report.runs == []


@pytest.mark.asyncio
async def test_no_matching_target_runs_nothing() -> None:
    docker = FakeDocker({})
    report = await _gate(docker, _config(), ["docs/x.md"])
    assert report.verdict == "no_matching_targets"
    assert docker.runs == []


@pytest.mark.asyncio
async def test_long_output_keeps_the_tail_but_parses_everything() -> None:
    noise = "\n".join(f"line {i}" for i in range(5000))
    red = (1, f"FAILED tests/a.py::test_x - boom\n{noise}")
    docker = FakeDocker({(HEAD_DIR, JVM): [red], (BASE_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    run = report.runs_for("head")[0]
    assert run.failed_tests == ["tests/a.py::test_x"]
    assert len(run.output_tail) <= 8000
    assert run.output_tail.endswith("line 4999")


@pytest.mark.asyncio
async def test_an_exhausted_time_budget_stops_running_and_never_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    import embry0.workflows.verification.runner as runner

    clock = iter([0.0])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock, 99999.0))
    docker = FakeDocker({(HEAD_DIR, JVM): [(0, "ok")], (BASE_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(), ["platform/api/Foo.java"])
    assert report.verdict == "error"
    assert "time budget" in report.note


# ── review of #341: what reaches the database, and what the names prove ──


@pytest.mark.asyncio
async def test_secrets_in_command_output_never_reach_the_stored_tail() -> None:
    leak = "XAI_API_KEY=xai-abcdef0123456789abcdef0123456789\nGITHUB_TOKEN=ghp_0123456789abcdefghijklmnopqrstuvwxyz"
    docker = FakeDocker({(HEAD_DIR, JVM): [(1, leak)], (BASE_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    tail = report.runs_for("head")[0].output_tail
    assert "xai-abcdef" not in tail and "ghp_0123" not in tail
    assert "***REDACTED***" in tail
    assert "XAI_API_KEY=" in tail  # the shape of the log survives


@pytest.mark.asyncio
async def test_a_file_level_runner_is_recorded_as_file_granular() -> None:
    docker = FakeDocker({(HEAD_DIR, JVM): [(1, " FAIL  src/a.test.tsx")], (BASE_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    assert report.runs_for("head")[0].name_granularity == "file"


@pytest.mark.asyncio
async def test_a_test_level_runner_is_recorded_as_test_granular() -> None:
    out = " FAIL  src/a.test.tsx > cart > shows who"
    docker = FakeDocker({(HEAD_DIR, JVM): [(1, out)], (BASE_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    run = report.runs_for("head")[0]
    assert run.name_granularity == "test"
    assert run.failed_tests == ["src/a.test.tsx > cart > shows who"]


@pytest.mark.asyncio
async def test_a_docker_client_that_does_not_strip_its_output_still_works() -> None:
    """Depending on run_cmd's .strip() was a silent dependency that made every
    target 'errored' for a caller that did not strip (review of #341): the
    prepare step's markers are read line by line, blank lines aside."""

    class UnstrippedDocker(FakeDocker):
        async def run_cmd(self, cmd, timeout: int = 60, *, max_bytes: int | None = None) -> str:
            return await super().run_cmd(cmd, timeout, max_bytes=max_bytes) + "\n\n"

    docker = UnstrippedDocker({(HEAD_DIR, JVM): [(0, "ok")], (BASE_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(), ["platform/api/Foo.java"])
    assert report.verdict == "passed"
    assert [r.outcome for r in report.runs] == ["passed", "passed"]


@pytest.fixture
def registered_secret():
    from embry0.safety.redaction import known_secret_values, register_known_secrets

    before = known_secret_values()
    register_known_secrets(["s3cr3t-nonstandard-value"])
    yield "s3cr3t-nonstandard-value"
    register_known_secrets(before, reset=True)


@pytest.mark.asyncio
async def test_a_registry_secret_never_reaches_the_stored_tail(registered_secret) -> None:
    """Spec 2026-09-29 §5: the registry plaintexts registered at startup are scrubbed by value,
    whatever their shape — the pattern list alone would pass this one through."""
    docker = FakeDocker(
        {(HEAD_DIR, JVM): [(1, f"clone with {registered_secret} failed")], (BASE_DIR, JVM): [(0, "ok")]}
    )
    report = await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    tail = report.runs_for("head")[0].output_tail
    assert registered_secret not in tail, tail
    assert "***REDACTED***" in tail


@pytest.mark.asyncio
async def test_a_secret_cut_by_the_tail_boundary_leaves_no_fragment(registered_secret) -> None:
    from embry0.workflows.verification.runner import STORED_TAIL_CHARS

    # The secret straddles the cut: only its last characters would fall inside the tail.
    body = registered_secret + "x" * (STORED_TAIL_CHARS - 10)
    docker = FakeDocker({(HEAD_DIR, JVM): [(1, body)], (BASE_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    tail = report.runs_for("head")[0].output_tail
    assert registered_secret[-8:] not in tail, tail[:40]


def _process(out: bytes, returncode: int = 0) -> SimpleNamespace:
    """A finished command that printed ``out``."""
    stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
    stdout.feed_data(out)
    stdout.feed_eof()
    stderr.feed_eof()

    async def wait() -> int:
        return returncode

    async def communicate(input: bytes | None = None) -> tuple[bytes, bytes]:
        # What a whole-output read does: the client this replaced called it.
        return await stdout.read(), await stderr.read()

    return SimpleNamespace(
        stdout=stdout, stderr=stderr, stdin=None, returncode=returncode, pid=4242, wait=wait, communicate=communicate
    )


@pytest.mark.asyncio
async def test_a_run_whose_log_tail_starts_inside_a_character_still_passes() -> None:
    """The run's log is read back with `tail -c`, which can cut a multi-byte character (#394)."""
    from embry0.execution.docker_client import DockerClient

    prepared = f"EMBRY0_VERIFY_HEAD={HEAD_SHA}\nEMBRY0_VERIFY_BASE={BASE_SHA}\n".encode()
    sweep = CLEAN_SWEEP.encode()
    log = "✓ 12 tests passed\n".encode()[1:]
    # prepare, a sweep, the head run, a sweep, its log, cleanup
    procs = [_process(prepared), _process(sweep), _process(b""), _process(sweep), _process(log), _process(b"")]
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, side_effect=procs):
        report = await run_gate(
            docker=DockerClient(),
            container_id="c1",
            config=_config(base_run="on_head_failure"),
            changed_files=["platform/api/Foo.java"],
            base_ref="main",
        )
    assert [r.outcome for r in report.runs] == ["passed"], [(r.outcome, r.output_tail) for r in report.runs]
    assert report.verdict == "passed"
    assert report.runs[0].output_tail == "�� 12 tests passed", report.runs[0].output_tail


# ── the verdict comes from the run's exit status, read by the orchestrator ──

PASS_LINE = "EMBRY0_VERIFY_RESULT exit=0 ms=5\n"


@pytest.mark.asyncio
async def test_a_run_that_exits_non_zero_fails_whatever_its_output_says() -> None:
    log = f"FAILED tests/test_a.py::test_a - boom\n{PASS_LINE}"
    docker = FakeDocker({(HEAD_DIR, JVM): [(1, log)], (BASE_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    head = report.runs_for("head")[0]
    assert (head.outcome, head.exit_code) == ("failed", 1), (
        f"the exec exited 1, so the run failed; a line saying exit 0 is only text: {head.outcome} {head.output_tail!r}"
    )
    assert head.failed_tests == ["tests/test_a.py::test_a"], head.failed_tests
    assert report.verdict == "new_failures"


@pytest.mark.asyncio
async def test_a_result_line_printed_on_the_exec_is_never_read_as_a_pass() -> None:
    """Nothing in the run prints on the exec's streams; a line there came from elsewhere, and is not a result."""
    printed = {(HEAD_DIR, JVM): (f"EMBRY0_VERIFY_RESULT exit=1 ms=9\n{PASS_LINE}", "")}
    docker = FakeDocker({(HEAD_DIR, JVM): [(0, "")], (BASE_DIR, JVM): [(0, "ok")]}, printed=printed)
    report = await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    head = report.runs_for("head")[0]
    assert head.outcome == "errored", f"output on the exec's own streams makes the run unusable: {head.outcome}"
    assert head.output_tail == "the command could not be started in the sandbox", head.output_tail
    assert report.verdict == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [124, 137])
async def test_a_run_stopped_by_its_timeout_is_timed_out(code: int) -> None:
    docker = FakeDocker({(HEAD_DIR, JVM): [(code, "")], (BASE_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    head = report.runs_for("head")[0]
    assert (head.outcome, head.exit_code) == ("timed_out", code), f"exit {code} is the in-sandbox timeout: {head}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stdout", "stderr"),
    [
        ("", "Error response from daemon: container c1 is not running\n"),
        ("bash: warning: setlocale: LC_ALL: cannot change locale\n", ""),
        ("\n", ""),
    ],
    ids=["a-daemon-message", "setup-output", "a-blank-line"],
)
async def test_anything_on_the_exec_streams_makes_a_green_run_errored(stdout: str, stderr: str) -> None:
    printed = {(HEAD_DIR, JVM): (stdout, stderr)}
    docker = FakeDocker({(HEAD_DIR, JVM): [(0, "1 passed")], (BASE_DIR, JVM): [(0, "ok")]}, printed=printed)
    report = await _gate(docker, _config(base_run="on_head_failure"), ["platform/api/Foo.java"])
    head = report.runs_for("head")[0]
    assert (head.outcome, head.output_tail) == ("errored", "the command could not be started in the sandbox"), (
        f"exit 0 with output on the exec's streams is not a pass: {head.outcome} {head.output_tail!r}"
    )


@pytest.mark.asyncio
async def test_a_run_directory_that_cannot_be_entered_is_errored_with_its_own_note() -> None:
    printed = {(BASE_DIR, JVM): ("EMBRY0_VERIFY_NO_RUN_DIR\n", "")}
    docker = FakeDocker({(HEAD_DIR, JVM): [(1, "boom")], (BASE_DIR, JVM): [(1, "")]}, printed=printed)
    report = await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    bases = report.runs_for("base")
    assert bases, f"head failed, so base runs: {[(r.side, r.outcome, r.output_tail) for r in report.runs]}"
    base = bases[0]
    assert base.outcome == "errored", f"a base that never ran is not a red base: {base.outcome}"
    assert base.output_tail == "the directory to run in could not be entered in the sandbox", base.output_tail
    assert report.verdict == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "stderr"),
    [
        (125, "Error response from daemon: container c1 is paused, unpause the container before exec\n"),
        (126, "OCI runtime exec failed: exec failed: unable to start container process: permission denied\n"),
        (127, 'OCI runtime exec failed: exec: "bash": executable file not found in $PATH: unknown\n'),
    ],
    ids=["125", "126", "127"],
)
async def test_a_docker_level_status_with_a_daemon_message_is_errored(code: int, stderr: str) -> None:
    printed = {(HEAD_DIR, JVM): ("", stderr)}
    docker = FakeDocker({(HEAD_DIR, JVM): [(code, "")], (BASE_DIR, JVM): [(0, "ok")]}, printed=printed)
    report = await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    head = report.runs_for("head")[0]
    assert head.outcome == "errored", f"exit {code} with a daemon message is docker's, not the command's: {head}"
    assert report.verdict == "error"


@pytest.mark.asyncio
async def test_a_docker_client_ended_by_a_signal_is_errored() -> None:
    """A negative status is the docker client's own death, not anything the command returned."""

    class SignalledDocker(FakeDocker):
        async def run_cmd_status(self, cmd, timeout: int = 60, *, max_bytes: int | None = None):
            code, out, err = await super().run_cmd_status(cmd, timeout, max_bytes=max_bytes)
            return (-9, out, err) if "EMBRY0_VERIFY_CMD" in cmd["env"] else (code, out, err)

    docker = SignalledDocker({(HEAD_DIR, JVM): [(0, "1 passed")], (BASE_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    head = report.runs_for("head")[0]
    assert head.outcome == "errored", f"a killed docker client says nothing about the run: {head}"


@pytest.mark.asyncio
async def test_a_run_without_an_exit_status_is_errored() -> None:
    """A docker client that hands back no status says nothing about the run."""

    class NoStatusDocker(FakeDocker):
        async def run_cmd_status(self, cmd, timeout: int = 60, *, max_bytes: int | None = None):
            code, out, err = await super().run_cmd_status(cmd, timeout, max_bytes=max_bytes)
            return (None, out, err) if "EMBRY0_VERIFY_CMD" in cmd["env"] else (code, out, err)

    docker = NoStatusDocker({(HEAD_DIR, JVM): [(0, "1 passed")], (BASE_DIR, JVM): [(0, "ok")]})
    try:
        report = await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    except TypeError as exc:
        pytest.fail(f"a run without an exit status crashed the gate instead of being recorded: {exc}")
    head = report.runs_for("head")[0]
    assert (head.outcome, head.exit_code) == ("errored", None), f"no status is no result: {head}"
    assert report.verdict == "error"


@pytest.mark.asyncio
async def test_a_log_read_that_prints_on_stderr_is_not_trusted() -> None:
    """The log read prints the log and nothing else; a warning beside it makes the log unread, not part of it."""
    red = (1, "FAILED tests/test_a.py::test_a - boom")
    docker = FakeDocker(
        {(HEAD_DIR, JVM): [red], (BASE_DIR, JVM): [(0, "ok")]}, log_stderr="tail: warning: the log is being written\n"
    )
    report = await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    head = report.runs_for("head")[0]
    assert (head.outcome, head.exit_code) == ("failed", 1), "the exit status still decides the run"
    assert head.failed_tests is None, f"names read from a log read that also printed on stderr: {head.failed_tests}"
    assert head.output_tail == "the run's log could not be read", head.output_tail


@pytest.mark.asyncio
async def test_a_silent_255_is_the_commands_own_status_when_the_sandbox_still_answers() -> None:
    docker = FakeDocker({(HEAD_DIR, JVM): [(255, "FAILED tests/t.py::test_z - boom")], (BASE_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    head = report.runs_for("head")[0]
    assert (head.outcome, head.exit_code) == ("failed", 255), (
        f"a sweep and a log read after it answered, so 255 is the command's: {head.outcome} {head.output_tail!r}"
    )
    assert head.failed_tests == ["tests/t.py::test_z"]


@pytest.mark.asyncio
async def test_a_silent_255_with_an_unreadable_log_is_errored() -> None:
    docker = FakeDocker({(HEAD_DIR, JVM): [(255, "")], (BASE_DIR, JVM): [(0, "ok")]}, log_read_fails=True)
    report = await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    head = report.runs_for("head")[0]
    assert head.outcome == "errored", f"255 and a sandbox that no longer answers is docker's status: {head}"
    assert head.output_tail == "the sandbox stopped answering during the run", head.output_tail


@pytest.mark.asyncio
async def test_a_silent_255_with_a_failed_sweep_after_it_is_errored() -> None:
    # The sweep before head passes; the one after it finds the sandbox gone.
    sweeps = [(0, CLEAN_SWEEP, ""), (255, "", "")]
    docker = FakeDocker({(HEAD_DIR, JVM): [(255, "")], (BASE_DIR, JVM): [(0, "ok")]}, sweeps=sweeps)
    report = await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    head = report.runs_for("head")[0]
    assert head.outcome == "errored", f"a run whose sandbox could not be checked afterwards is not judged: {head}"
    assert "after the run" in head.output_tail, head.output_tail


@pytest.mark.asyncio
async def test_a_runs_duration_is_the_orchestrators_time_around_its_exec() -> None:
    docker = FakeDocker(
        {(HEAD_DIR, JVM): [(1, "boom\nEMBRY0_VERIFY_RESULT exit=1 ms=5")], (BASE_DIR, JVM): [(0, "ok")]}
    )
    report = await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    durations = [r.duration_ms for r in report.runs]
    assert durations == [1234, 1234], f"each run's exec took 1.234 s on the orchestrator's clock: {durations}"


# ── a sweep of the sandbox's processes before and after every run ──


@pytest.mark.asyncio
async def test_every_run_is_swept_before_and_after_and_its_log_read_last() -> None:
    red = (1, "FAILED tests/test_a.py::test_a - boom")
    docker = FakeDocker({(HEAD_DIR, JVM): [red], (BASE_DIR, JVM): [(0, "ok")]})
    report = await _gate(docker, _config(head_reruns=1), ["platform/api/Foo.java"])
    assert [(r.side, r.attempt) for r in report.runs] == [("head", 1), ("base", 1), ("head", 2)]
    one = ["sweep", "run", "sweep", "log"]
    assert docker.steps == ["prepare", *one, *one, *one, "cleanup"], (
        f"every run (head, base, each attempt) is swept before and after, and its log read after the sweep: "
        f"{docker.steps}"
    )
    assert set(docker.sweep_users) == {None}, f"the sweep runs as the container's default user: {docker.sweep_users}"


@pytest.mark.asyncio
async def test_a_process_that_survives_the_sweep_before_a_run_stops_the_run() -> None:
    survivor = (0, "EMBRY0_SWEEP stopped=1 passes=20 survivors=1 unread=0 pids=77\n", "")
    docker = FakeDocker({(HEAD_DIR, JVM): [(0, "ok")], (BASE_DIR, JVM): [(0, "ok")]}, sweeps=[survivor])
    report = await _gate(docker, _config(base_run="on_head_failure"), ["platform/api/Foo.java"])
    head = report.runs_for("head")[0]
    assert head.outcome == "errored", f"a process left running before the run makes it unusable: {head.outcome}"
    assert head.output_tail == "not run: before the run, 1 process could not be stopped (pid 77)", head.output_tail
    assert docker.runs == [], f"the command is not started in a sandbox that could not be cleared: {docker.runs}"
    assert report.verdict == "error"


@pytest.mark.asyncio
async def test_a_process_that_survives_the_sweep_after_a_run_voids_its_result() -> None:
    sweeps = [(0, CLEAN_SWEEP, ""), (0, "EMBRY0_SWEEP stopped=2 passes=20 survivors=1 unread=0 pids=91\n", "")]
    docker = FakeDocker({(HEAD_DIR, JVM): [(0, "ok")], (BASE_DIR, JVM): [(0, "ok")]}, sweeps=sweeps)
    report = await _gate(docker, _config(base_run="on_head_failure"), ["platform/api/Foo.java"])
    head = report.runs_for("head")[0]
    assert head.outcome == "errored", f"a run that left a process nothing could stop is not a pass: {head.outcome}"
    assert head.output_tail == "not judged: after the run, 1 process could not be stopped (pid 91)", head.output_tail
    assert "log" not in docker.steps, f"the log is not read while a process could still write it: {docker.steps}"


@pytest.mark.asyncio
async def test_a_wedged_run_is_still_swept_after() -> None:
    docker = FakeDocker({(HEAD_DIR, JVM): [(-2, "")], (BASE_DIR, JVM): [(0, "ok")]})
    await _gate(docker, _config(head_reruns=0), ["platform/api/Foo.java"])
    assert docker.steps[:4] == ["prepare", "sweep", "run", "sweep"], (
        f"a run that did not return is still running in the sandbox: the sweep after it stops it: {docker.steps}"
    )


# ── the prepare step names exactly one head and one base ──

OTHER_SHA = "c" * 40


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prepared",
    [
        f"EMBRY0_VERIFY_HEAD={HEAD_SHA}\nEMBRY0_VERIFY_BASE={BASE_SHA}\nEMBRY0_VERIFY_HEAD={OTHER_SHA}",
        f"EMBRY0_VERIFY_HEAD={HEAD_SHA}\nEMBRY0_VERIFY_BASE={BASE_SHA}\nEMBRY0_VERIFY_BASE={OTHER_SHA}",
        f"EMBRY0_VERIFY_HEAD=abc1234\nEMBRY0_VERIFY_HEAD={HEAD_SHA}\nEMBRY0_VERIFY_BASE={BASE_SHA}",
        f"EMBRY0_VERIFY_HEAD={HEAD_SHA}\nEMBRY0_VERIFY_BASE={BASE_SHA}\nEMBRY0_VERIFY_BASE={BASE_SHA[:39]}",
    ],
    ids=["two-heads", "two-bases", "a-short-head-beside-a-full-one", "a-short-base-beside-a-full-one"],
)
async def test_a_prepare_step_that_names_more_than_one_commit_errors(prepared: str) -> None:
    docker = FakeDocker({(HEAD_DIR, JVM): [(0, "ok")], (BASE_DIR, JVM): [(0, "ok")]}, prepared=prepared)
    report = await _gate(docker, _config(), ["platform/api/Foo.java"])
    assert report.verdict == "error", f"two answers for one commit are not chosen between: {report.verdict}"
    assert report.runs == [] and docker.runs == [], "nothing runs on a head or base that is not known"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prepared",
    [
        f"EMBRY0_VERIFY_HEAD=abc1234\nEMBRY0_VERIFY_BASE={BASE_SHA}",
        f"EMBRY0_VERIFY_HEAD={HEAD_SHA}",
        f"EMBRY0_VERIFY_HEAD={HEAD_SHA.upper()}\nEMBRY0_VERIFY_BASE={BASE_SHA}",
        f"EMBRY0_VERIFY_HEAD={HEAD_SHA} \nEMBRY0_VERIFY_BASE={BASE_SHA}",
    ],
    ids=["a-short-sha", "no-base", "upper-case", "trailing-space"],
)
async def test_a_prepare_step_without_one_full_sha_of_each_errors(prepared: str) -> None:
    docker = FakeDocker({(HEAD_DIR, JVM): [(0, "ok")], (BASE_DIR, JVM): [(0, "ok")]}, prepared=prepared)
    report = await _gate(docker, _config(), ["platform/api/Foo.java"])
    assert report.verdict == "error" and report.runs == [], f"{prepared!r} is not one full sha of each"


@pytest.mark.asyncio
async def test_the_head_sha_reported_is_the_one_the_prepare_step_parsed() -> None:
    docker = FakeDocker(
        {(HEAD_DIR, JVM): [(0, "ok")], (BASE_DIR, JVM): [(0, "ok")]},
        prepared=f"EMBRY0_VERIFY_BASE={BASE_SHA}\nEMBRY0_VERIFY_HEAD={OTHER_SHA}\n",
    )
    report = await _gate(docker, _config(), ["platform/api/Foo.java"])
    assert (report.head_sha, report.base_sha) == (OTHER_SHA, BASE_SHA), (report.head_sha, report.base_sha)
    assert report.to_dict()["head_sha"] == OTHER_SHA, "the stored result carries the parsed head"
