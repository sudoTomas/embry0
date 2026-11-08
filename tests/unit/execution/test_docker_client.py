import asyncio
import inspect
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from embry0.execution.docker_client import DockerClient


@pytest.fixture
def docker() -> DockerClient:
    return DockerClient(
        docker_host="tcp://dind:2376",
        tls_verify=True,
        cert_path="/certs/client",
    )


def test_build_base_cmd(docker: DockerClient):
    """Base command includes host and TLS flags."""
    cmd = docker._build_base_cmd()
    assert "docker" in cmd
    assert "--host" in cmd
    assert "tcp://dind:2376" in cmd
    assert "--tlsverify" in cmd
    assert "--tlscacert=/certs/client/ca.pem" in cmd


def test_build_base_cmd_no_tls():
    """Without TLS, no TLS flags."""
    client = DockerClient()
    cmd = client._build_base_cmd()
    assert cmd == ["docker"]


def test_build_run_cmd(docker: DockerClient):
    """Run command includes security and resource flags."""
    cmd = docker.build_run_cmd(
        image="embry0-sandbox:latest",
        name="sandbox-job-123",
        network="sandbox-restricted",
        memory="8g",
        cpus="4",
        pids_limit=256,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
        read_only=True,
    )
    assert "run" in cmd
    assert "-d" in cmd
    assert "--name" in cmd
    assert "sandbox-job-123" in cmd
    assert "--cap-drop=ALL" in cmd
    assert "--security-opt=no-new-privileges" in cmd
    assert "--memory=8g" in cmd
    assert "--cpus=4" in cmd
    assert "--pids-limit=256" in cmd
    assert "--read-only" in cmd
    assert "--network=sandbox-restricted" in cmd
    assert "embry0-sandbox:latest" in cmd
    assert cmd[-1] == "infinity"  # sleep infinity


def test_build_run_cmd_volumes(docker: DockerClient):
    """Volumes are passed as -v flags, appearing before image."""
    cmd = docker.build_run_cmd(
        image="embry0-sandbox:latest",
        name="sandbox-job-123",
        volumes=["/host/path:/container/path:ro", "/data:/data"],
    )
    # Both volumes appear as -v flag pairs
    assert "-v" in cmd
    v_pairs = [cmd[i + 1] for i, t in enumerate(cmd) if t == "-v"]
    assert "/host/path:/container/path:ro" in v_pairs
    assert "/data:/data" in v_pairs
    # Image must come after all -v flags
    image_idx = cmd.index("embry0-sandbox:latest")
    for v in v_pairs:
        assert cmd.index(v) < image_idx


def test_build_run_cmd_no_volumes(docker: DockerClient):
    """Without volumes parameter, no -v flags appear."""
    cmd = docker.build_run_cmd(image="embry0-sandbox:latest", name="sandbox-job-123")
    assert "-v" not in cmd


def test_build_run_cmd_extra_hosts(docker: DockerClient):
    """extra_hosts maps to --add-host=name:ip flags."""
    cmd = docker.build_run_cmd(
        image="embry0-sandbox:latest",
        name="sandbox-1",
        extra_hosts={"dind": "172.24.0.3", "minio-proxy": "172.24.0.7"},
    )
    assert "--add-host=dind:172.24.0.3" in cmd
    assert "--add-host=minio-proxy:172.24.0.7" in cmd


def test_build_run_cmd_no_extra_hosts(docker: DockerClient):
    """Without extra_hosts, no --add-host flag is emitted."""
    cmd = docker.build_run_cmd(image="embry0-sandbox:latest", name="sandbox-1")
    assert not any(t.startswith("--add-host") for t in cmd)


def test_build_run_cmd_unqualified_image_omits_pull(docker: DockerClient):
    """A bare image name (no registry segment) must NOT add --pull=always —
    the image only exists locally and a pull would fail."""
    cmd = docker.build_run_cmd(image="embry0-sandbox:latest", name="sandbox-1")
    assert "--pull=always" not in cmd


def test_build_run_cmd_registry_qualified_image_forces_pull(docker: DockerClient):
    """When the image is qualified against the in-stack registry (host
    contains `:` for the port), every launch must force a fresh pull. This
    prevents DinD from serving a stale `latest` after the host rebuilds
    and init-push-images refreshes the registry — the silent failure
    mode that masked the QA pipeline's runner-import crash on 2026-05-06."""
    cmd = docker.build_run_cmd(image="registry:5000/embry0-sandbox:latest", name="sandbox-1")
    assert "--pull=always" in cmd
    # And it must precede the image arg / "run -d" must still be there.
    assert "run" in cmd
    assert cmd.index("--pull=always") < cmd.index("registry:5000/embry0-sandbox:latest")


def test_build_run_cmd_dotted_registry_host_forces_pull(docker: DockerClient):
    """A registry hostname with a `.` (e.g. ghcr.io/...) is also qualified."""
    cmd = docker.build_run_cmd(image="ghcr.io/acme-corp/embry0-sandbox:latest", name="sandbox-1")
    assert "--pull=always" in cmd


def test_build_run_cmd_localhost_registry_forces_pull(docker: DockerClient):
    """A `localhost/...` qualified image (Docker's special form) also pulls."""
    cmd = docker.build_run_cmd(image="localhost/embry0-sandbox:latest", name="sandbox-1")
    assert "--pull=always" in cmd


def test_build_run_cmd_tmpfs_mounts_emit_tmpfs_flags(docker: DockerClient):
    """tmpfs_mounts list must turn into per-path --tmpfs flags so callers
    can overlay an in-memory dir on top of a shared volume mount."""
    cmd = docker.build_run_cmd(
        image="img:latest",
        name="sb-1",
        tmpfs_mounts=["/workspace/.qa", "/var/run/agent"],
    )
    assert "--tmpfs" in cmd
    # Tmpfs MUST include mode=1777 so the non-root sandbox user can create
    # files in it (default Docker tmpfs is mode 0755 owned by root, which
    # makes mkdir/touch fail with Permission denied for the `agent` user).
    assert "/workspace/.qa:rw,nosuid,mode=1777" in cmd
    assert "/var/run/agent:rw,nosuid,mode=1777" in cmd
    # Each tmpfs is preceded by --tmpfs. The /tmp + /home/agent/.claude
    # built-ins still use ":rw,nosuid"; the explicit tmpfs_mounts entries
    # use ":rw,nosuid,mode=1777". So every value ends with one of these.
    idxs = [i for i, t in enumerate(cmd) if t == "--tmpfs"]
    assert all(cmd[i + 1].endswith(":rw,nosuid") or cmd[i + 1].endswith(":rw,nosuid,mode=1777") for i in idxs)


def test_build_run_cmd_no_tmpfs_when_omitted(docker: DockerClient):
    """When neither read_only nor tmpfs_mounts trigger a tmpfs, none appear."""
    cmd = docker.build_run_cmd(image="img:latest", name="sb-1", read_only=False)
    assert not any(t.startswith("--tmpfs") for t in cmd)
    # And explicit None is the same as omitted.
    cmd2 = docker.build_run_cmd(image="img:latest", name="sb-1", read_only=False, tmpfs_mounts=None)
    assert cmd == cmd2


# ---------------------------------------------------------------------------
# _scrub_cmd_for_log: redact secret -e KEY=VAL pairs before logging argv.
# ---------------------------------------------------------------------------


def test_scrub_cmd_redacts_oauth_token():
    """The OAuth token passed to sandbox launches must not reach logs."""
    from embry0.execution.docker_client import _scrub_cmd_for_log

    cmd = [
        "docker",
        "run",
        "-d",
        "-e",
        "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-secret-value",
        "-e",
        "QA_JOB_ID=job-1",
        "registry:5000/embry0-sandbox:latest",
    ]
    scrubbed = _scrub_cmd_for_log(cmd)
    assert "CLAUDE_CODE_OAUTH_TOKEN=***REDACTED***" in scrubbed
    assert "sk-ant-oat01-secret-value" not in " ".join(scrubbed)
    # Default-deny: env values not on the safe-list are redacted too.
    assert "QA_JOB_ID=***REDACTED***" in scrubbed
    # Length and order preserved (consumers may index into the array).
    assert len(scrubbed) == len(cmd)
    assert scrubbed[4] == "CLAUDE_CODE_OAUTH_TOKEN=***REDACTED***"


def test_scrub_cmd_redacts_known_secret_keys():
    from embry0.execution.docker_client import _scrub_cmd_for_log

    cmd = [
        "docker",
        "run",
        "-e",
        "ANTHROPIC_API_KEY=sk-ant-api...",
        "-e",
        "GITHUB_TOKEN=ghp_...",
        "-e",
        "EMBRY0_SANDBOX_TOKEN=tok-...",
        "-e",
        "POSTGRES_PASSWORD=p4ss",
        "-e",
        "ENVIRONMENT_SECRET_KEY=k",
        "-e",
        "PROXY_ADMIN_TOKEN=admin",
        "img",
    ]
    scrubbed = " ".join(_scrub_cmd_for_log(cmd))
    for needle in ("sk-ant-api...", "ghp_", "tok-...", "p4ss", "ENVIRONMENT_SECRET_KEY=k", "PROXY_ADMIN_TOKEN=admin"):
        assert needle not in scrubbed, f"leaked: {needle!r}"


def test_scrub_cmd_default_deny_redacts_unlisted_secrets():
    """Default-deny: every -e value is redacted except the docker safe-list.
    Regression for the leak where AZURE/CF/DB/etc. secrets reached logs because
    they were never in an allowlist."""
    from embry0.execution.docker_client import _scrub_cmd_for_log

    cmd = [
        "docker",
        "run",
        "-e",
        "DOCKER_HOST=tcp://dind:2376",
        "-e",
        "AZURE_CLIENT_SECRET=supersecret",
        "-e",
        "FOO=bar",
        "img",
    ]
    scrubbed = _scrub_cmd_for_log(cmd)
    # safe docker connection key stays visible
    assert "DOCKER_HOST=tcp://dind:2376" in scrubbed
    # a previously-unlisted secret is now redacted
    assert "AZURE_CLIENT_SECRET=***REDACTED***" in scrubbed
    assert "supersecret" not in " ".join(scrubbed)
    # even an unknown non-secret is redacted (fail-safe)
    assert "FOO=***REDACTED***" in scrubbed


def test_scrub_cmd_handles_dangling_e_flag():
    """`-e` at the end with nothing after it: don't crash, don't redact spuriously."""
    from embry0.execution.docker_client import _scrub_cmd_for_log

    cmd = ["docker", "run", "img", "-e"]
    assert _scrub_cmd_for_log(cmd) == cmd


def test_scrub_cmd_handles_e_without_equals():
    """`-e KEY` (no value) — docker reads it from the host env. Pass through."""
    from embry0.execution.docker_client import _scrub_cmd_for_log

    cmd = ["docker", "run", "-e", "GITHUB_TOKEN", "img"]
    # No `=` in the value, so the partition fallback kicks in and we just
    # leave the token alone (docker would resolve it from env at run time).
    assert _scrub_cmd_for_log(cmd) == cmd


def test_build_exec_cmd(docker: DockerClient):
    """Exec command targets container by name."""
    cmd = docker.build_exec_cmd(
        container="sandbox-job-123",
        command=["python", "-m", "embry0.sandbox.runner", "--config", "{}"],
    )
    assert "exec" in cmd
    assert "sandbox-job-123" in cmd
    assert "python" in cmd


def test_build_network_connect_cmd(docker: DockerClient):
    """Network connect command."""
    cmd = docker.build_network_cmd("connect", "sandbox-internet", "sandbox-job-123")
    assert "network" in cmd
    assert "connect" in cmd
    assert "sandbox-internet" in cmd
    assert "sandbox-job-123" in cmd


def test_build_network_disconnect_cmd(docker: DockerClient):
    """Network disconnect command."""
    cmd = docker.build_network_cmd("disconnect", "sandbox-internet", "sandbox-job-123")
    assert "disconnect" in cmd


@pytest.mark.asyncio
async def test_run_cmd_success():
    """run_cmd returns stdout on success."""
    client = DockerClient()
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = _Proc(_Pipe(data=b"container-id\n"))

        result = await client.run_cmd(["docker", "ps"])
        assert result == "container-id"


def test_a_long_script_is_logged_by_its_start_and_its_end():
    """embry0's git prelude rides every sandbox git exec; a failure log keeps the step, not 5 KB of prelude."""
    from embry0.execution.docker_client import _scrub_cmd_for_log

    script = "PRELUDE-START " + "x" * 6000 + " the step's own commands"
    logged = _scrub_cmd_for_log(["docker", "exec", "c1", "bash", "-c", script, "embry0-git", "status"])
    assert logged[:5] == ["docker", "exec", "c1", "bash", "-c"] and logged[6:] == ["embry0-git", "status"]
    assert logged[5].startswith("PRELUDE-START") and logged[5].endswith("the step's own commands")
    assert len(logged[5]) < 1500, f"a {len(script)}-char script was logged whole"
    assert _scrub_cmd_for_log(["docker", "ps"]) == ["docker", "ps"]


@pytest.mark.asyncio
async def test_run_cmd_writes_stdin_data_to_the_command(tmp_path):
    """A secret goes to a docker exec on stdin, never on its command line (sandbox_git.configure)."""
    import inspect

    assert "stdin_data" in inspect.signature(DockerClient.run_cmd).parameters, (
        "run_cmd must accept stdin_data so a secret can be passed off-argv"
    )
    out = tmp_path / "got"
    client = DockerClient()
    result = await client.run_cmd(["sh", "-c", f"cat > {out}; echo done"], stdin_data=b"bearer-0001\n")
    assert result == "done"
    assert out.read_bytes() == b"bearer-0001\n", "stdin_data did not reach the command's stdin"


class _Pipe:
    """A process pipe: hands out ``total`` bytes (``fill``) in whatever sizes are asked for, then EOF.

    ``requests`` records every size asked for (-1 is a whole-output read);
    ``hang`` never answers, like a command that prints nothing and never exits.
    """

    def __init__(self, total: int = 0, data: bytes = b"", fill: bytes = b"x", hang: bool = False) -> None:
        self._data = data
        self._total = total if not data else len(data)
        self._fill = fill
        self._hang = hang
        self.handed = 0
        self.requests: list[int] = []

    async def read(self, n: int = -1) -> bytes:
        self.requests.append(n)
        if self._hang:
            await asyncio.sleep(3600)
        if self.handed >= self._total:
            return b""
        k = self._total - self.handed if n < 0 else min(n, self._total - self.handed)
        chunk = self._data[self.handed : self.handed + k] if self._data else self._fill * k
        self.handed += k
        return chunk


class _Proc:
    def __init__(self, stdout: _Pipe, stderr: _Pipe | None = None, returncode: int = 0) -> None:
        self.stdout, self.stderr = stdout, stderr or _Pipe()
        self.returncode: int | None = None
        self._rc, self.killed, self.pid = returncode, False, 4242

    async def wait(self) -> int:
        self.returncode = -9 if self.killed else self._rc
        return self.returncode

    def kill(self) -> None:
        self.killed = True

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        # What a whole-output read does (and what run_cmd_bytes must not): drain both pipes.
        out, err = await self.stdout.read(-1), await self.stderr.read(-1)
        await self.wait()
        return out, err


def _too_large_error() -> type[Exception]:
    from embry0.execution import docker_client

    err = getattr(docker_client, "OutputTooLargeError", None)
    assert err is not None, "run_cmd_bytes must refuse output past its cap (docker_client.OutputTooLargeError)"
    return err


@pytest.mark.asyncio
async def test_run_cmd_bytes_returns_raw_stdout_without_decoding():
    """A file read out of a sandbox is binary; run_cmd_bytes must not decode or strip it (sandbox_files)."""
    assert hasattr(DockerClient, "run_cmd_bytes"), "DockerClient must expose run_cmd_bytes for binary reads"
    raw = b"\x89PNG\r\n\x1a\n\x00\xff not text  "
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = _Proc(_Pipe(data=raw))
        result = await DockerClient().run_cmd_bytes(["docker", "exec", "c", "cat", "/x"])
    assert result == raw, "bytes returned verbatim, not decoded or stripped"


@pytest.mark.asyncio
async def test_run_cmd_bytes_raises_on_non_zero_exit():
    assert hasattr(DockerClient, "run_cmd_bytes"), "DockerClient must expose run_cmd_bytes for binary reads"
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = _Proc(_Pipe(), _Pipe(data=b"embry0-read-refused: not a regular file"), returncode=3)
        with pytest.raises(RuntimeError, match="not a regular file"):
            await DockerClient().run_cmd_bytes(["docker", "exec", "c", "false"])


@pytest.mark.asyncio
async def test_run_cmd_bytes_never_buffers_past_its_cap():
    """A process that keeps writing (8 MiB here) is killed at the cap; the orchestrator never holds more than cap+1 bytes."""
    OutputTooLargeError = _too_large_error()
    stdout = _Pipe(total=8 * 1024 * 1024)
    proc = _Proc(stdout)
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = proc
        with pytest.raises(OutputTooLargeError):
            await DockerClient().run_cmd_bytes(["docker", "exec", "c", "cat", "/x"], max_bytes=1024)
    assert stdout.handed <= 1024 + 1, f"read {stdout.handed} bytes from the pipe; the cap is 1024"
    assert proc.killed, "the command is killed once it passes the cap"


@pytest.mark.asyncio
async def test_run_cmd_bytes_returns_output_at_exactly_the_cap():
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = _Proc(_Pipe(total=1024))
        assert len(await DockerClient().run_cmd_bytes(["docker", "x"], max_bytes=1024)) == 1024


@pytest.mark.asyncio
async def test_run_cmd_bytes_cap_holds_against_a_real_process():
    """A real pipe: 8 MiB written, a 1 KiB cap, refused promptly."""
    OutputTooLargeError = _too_large_error()
    with pytest.raises(OutputTooLargeError):
        await DockerClient().run_cmd_bytes(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * (8 << 20))"], timeout=20, max_bytes=1024
        )


@pytest.mark.asyncio
async def test_run_cmd_status_answers_a_failed_command_with_its_status_instead_of_raising():
    """A test run's exit status IS its result: a non-zero exit comes back with both outputs, never as an error."""
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = _Proc(_Pipe(data=b"out\n"), _Pipe(data=b"err\n"), returncode=3)
        try:
            result = await _reader(DockerClient(), "run_cmd_status")(["docker", "exec", "c", "false"])
        except RuntimeError as exc:
            pytest.fail(f"run_cmd_status raised on a non-zero exit: {exc}")
    assert result == (3, "out\n", "err\n"), f"(returncode, stdout, stderr), as printed: {result!r}"


@pytest.mark.asyncio
async def test_run_cmd_status_returns_output_as_printed():
    """Not stripped, so a caller can tell nothing from a newline; a cut character is replaced, not refused (#394)."""
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = _Proc(_Pipe(data="é\n".encode()[1:]), _Pipe(data=b"\n"))
        result = await _reader(DockerClient(), "run_cmd_status")(["docker", "exec", "c", "x"])
    assert result == (0, "\ufffd\n", "\n"), result


@pytest.mark.asyncio
async def test_run_cmd_status_reads_through_the_one_reader(monkeypatch):
    """No second reader: run_cmd_status reads with collect_output, under the cap its caller names."""
    from embry0.execution import docker_client

    seen: list[int] = []
    real = docker_client.collect_output

    async def spy(proc: Any, **kwargs: Any) -> tuple[bytes, bytes]:
        seen.append(kwargs["max_bytes"])
        return await real(proc, **kwargs)

    monkeypatch.setattr(docker_client, "collect_output", spy)
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = _Proc(_Pipe(data=b"ok"))
        await _reader(DockerClient(), "run_cmd_status")(["docker", "x"], max_bytes=4096)
    assert seen == [4096], f"run_cmd_status must read through collect_output with its cap: {seen}"


@pytest.mark.asyncio
async def test_run_cmd_status_never_answers_without_an_exit_status():
    """A command reaped without a status has no answer to give: not 0, not anything else."""

    class _NoStatus(_Proc):
        async def wait(self) -> int:
            return 0  # returncode is left unset

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = _NoStatus(_Pipe(data=b""))
        with pytest.raises(RuntimeError, match="without an exit status"):
            await _reader(DockerClient(), "run_cmd_status")(["docker", "exec", "c", "true"])


@pytest.mark.asyncio
async def test_run_cmd_status_kills_a_command_that_runs_past_its_timeout(monkeypatch):
    proc = _Proc(_Pipe(hang=True), _Pipe(hang=True))

    async def _fake_create(*args: Any, **kwargs: Any) -> _Proc:
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)
    with pytest.raises(TimeoutError, match="timed out after 1s"):
        await _reader(DockerClient(), "run_cmd_status")(["docker", "exec", "c", "sleep", "9"], timeout=1)
    assert proc.killed and proc.returncode is not None, "a command past its timeout is killed and reaped"


def _reader(client: DockerClient, name: str) -> Any:
    """A DockerClient method that reads a command's output; one that is missing fails with its name."""
    method = getattr(client, name, None)
    assert method is not None, f"DockerClient.{name} is missing"
    return method


def _bounded(name: str) -> Any:
    """A constant docker_client must define to bound what a command can make embry0 hold."""
    from embry0.execution import docker_client

    value = getattr(docker_client, name, None)
    assert value is not None, f"docker_client must define {name}: no docker command may be read without a cap"
    return value


def _run_cmd_takes_a_cap() -> None:
    params = inspect.signature(DockerClient.run_cmd).parameters
    assert "max_bytes" in params and "keep_tail" in params, (
        "run_cmd must cap the stdout it holds (max_bytes), or keep only its tail (keep_tail)"
    )


@pytest.mark.asyncio
async def test_run_cmd_stops_a_command_that_prints_past_its_cap():
    """A process that keeps writing (8 MiB here) is stopped at the cap: the pipe is never asked for more than cap+1."""
    from structlog.testing import capture_logs

    _run_cmd_takes_a_cap()
    OutputTooLargeError = _too_large_error()
    stdout = _Pipe(total=8 * 1024 * 1024)
    proc = _Proc(stdout)
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc, capture_logs() as logs:
        mock_proc.return_value = proc
        with pytest.raises(OutputTooLargeError) as caught:
            await DockerClient().run_cmd(
                ["docker", "exec", "c", "git", "status"], max_bytes=1024, log_context={"phase": "probe"}
            )
    assert isinstance(caught.value, RuntimeError), "an over-cap command must fail its step like any failed command"
    assert stdout.handed <= 1024 + 1, f"read {stdout.handed} bytes from the pipe; the cap is 1024"
    assert -1 not in stdout.requests, "stdout must be read in chunks, never whole"
    assert proc.killed, "the command is killed once it passes the cap"
    assert proc.returncode is not None, "the killed command is reaped"
    warned = [e for e in logs if e["event"] == "docker_cmd_too_large"]
    assert len(warned) == 1, f"one warning names the stopped command: {logs}"
    assert warned[0]["log_level"] == "warning" and warned[0]["max_bytes"] == 1024 and warned[0]["phase"] == "probe"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["run_cmd", "run_cmd_bytes", "run_cmd_status"])
async def test_a_command_is_bounded_without_a_cap_of_its_own(method: str):
    """A caller that names no cap still gets one: DEFAULT_MAX_OUTPUT_BYTES."""
    default = _bounded("DEFAULT_MAX_OUTPUT_BYTES")
    OutputTooLargeError = _too_large_error()
    stdout = _Pipe(total=default + 1024 * 1024)
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = _Proc(stdout)
        with pytest.raises(OutputTooLargeError):
            await _reader(DockerClient(), method)(["docker", "exec", "c", "cat", "/x"])
    assert stdout.handed <= default + 1, f"{method} read {stdout.handed} bytes; the default cap is {default}"


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", [None, 0, -1, True, 1.5])
async def test_no_command_runs_without_a_positive_cap(cap: Any):
    """Nothing can ask for an unbounded read: a missing or non-positive cap is refused before the command starts."""
    _run_cmd_takes_a_cap()
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        for method in ("run_cmd", "run_cmd_bytes", "run_cmd_status"):
            with pytest.raises(ValueError, match="max_bytes"):
                await _reader(DockerClient(), method)(["docker", "x"], max_bytes=cap)
    mock_proc.assert_not_called()


@pytest.mark.asyncio
async def test_a_cap_above_the_ceiling_is_refused():
    """A cap bounds what is held only up to MAX_OUTPUT_CAP_BYTES: a larger one is refused, not trusted."""
    ceiling = _bounded("MAX_OUTPUT_CAP_BYTES")
    assert ceiling >= _bounded("DAEMON_LISTING_MAX_BYTES"), "every named cap must fit under the ceiling"
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = _Proc(_Pipe(data=b"ok\n"))
        for method in ("run_cmd", "run_cmd_bytes", "run_cmd_status"):
            with pytest.raises(ValueError, match="max_bytes"):
                await _reader(DockerClient(), method)(["docker", "x"], max_bytes=ceiling + 1)
        assert mock_proc.call_count == 0, "a command with a refused cap must not start"
        assert await DockerClient().run_cmd(["docker", "x"], max_bytes=ceiling) == "ok", "the ceiling itself is a cap"


@pytest.mark.asyncio
async def test_a_log_cut_inside_a_character_is_read_not_refused():
    """`tail -c` can start a log inside a multi-byte character (#394): it is read, the cut bytes replaced."""
    log = "é ready on :3000\nEMBRY0_VERIFY_RESULT exit=0 ms=5\n".encode()[1:]  # starts on é's second byte
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = _Proc(_Pipe(data=log))
        try:
            out = await DockerClient().run_cmd(["docker", "exec", "c", "tail", "-c", "8192", "/x.log"])
        except UnicodeDecodeError as exc:
            pytest.fail(f"a log cut inside a character must still be read: {exc}")
    assert out == "\ufffd ready on :3000\nEMBRY0_VERIFY_RESULT exit=0 ms=5", out


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["run_cmd", "run_cmd_bytes", "run_cmd_status"])
async def test_a_real_process_past_the_cap_is_stopped_and_reaped_at_once(method: str):
    """A real pipe: 8 MiB written, a 1 KiB cap. The killed command is reaped at once, not logged as stuck after 5 s."""
    import time

    from structlog.testing import capture_logs

    if method == "run_cmd":
        _run_cmd_takes_a_cap()
    OutputTooLargeError = _too_large_error()
    started = time.monotonic()
    with capture_logs() as logs, pytest.raises(OutputTooLargeError):
        await _reader(DockerClient(), method)(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * (8 << 20))"], timeout=20, max_bytes=1024
        )
    elapsed = time.monotonic() - started
    assert elapsed < 3, f"{method}: the stopped command was reaped only after {elapsed:.1f}s"
    stuck = [e for e in logs if str(e["event"]).endswith("kill_stuck")]
    assert not stuck, f"{method}: a command that was killed and reaped was logged as stuck: {stuck}"


@pytest.mark.asyncio
async def test_a_flooded_stderr_is_drained_and_the_command_still_succeeds():
    """stderr is read to its end in chunks and dropped past what is kept: a chatty command neither blocks nor fails."""
    stderr = _Pipe(total=8 * 1024 * 1024, fill=b"e")
    proc = _Proc(_Pipe(data=b"ok\n"), stderr)
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = proc
        assert await DockerClient().run_cmd(["docker", "exec", "c", "make"]) == "ok"
    assert -1 not in stderr.requests, "stderr must be read in chunks, never whole"
    assert stderr.handed == 8 * 1024 * 1024, "stderr must be drained to its end"
    assert not proc.killed, "a command that exits 0 is not stopped for its stderr"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["run_cmd", "run_cmd_bytes"])
async def test_a_failure_keeps_the_start_and_the_end_of_a_flooded_stderr(method: str):
    """A failing build prints its cause last: the error keeps stderr's first 64 KiB and last 16 KiB, never all of it."""
    flood = b"FIRST LINE\n" + b"e" * (4 * 1024 * 1024) + b"\nERROR: the real cause"
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = _Proc(_Pipe(), _Pipe(data=flood), returncode=1)
        with pytest.raises(RuntimeError) as caught:
            await getattr(DockerClient(), method)(["docker", "build", "."])
    message = str(caught.value)
    assert "FIRST LINE" in message, "the error must keep the start of stderr"
    assert "ERROR: the real cause" in message, "the error must keep the end of stderr, where a build says why"
    assert len(message) < 82 * 1024, f"the error carried {len(message)} characters of stderr"
    assert "bytes not kept" in message, "the error must say that part of stderr was dropped"


@pytest.mark.asyncio
async def test_keep_tail_returns_the_end_of_a_long_log_without_stopping_it():
    """An install's output is a log: read to its end, only its last bytes kept and returned."""
    _run_cmd_takes_a_cap()
    log = b"x" * (8 * 1024 * 1024) + b"\nadded 1450 packages"
    stdout = _Pipe(data=log)
    proc = _Proc(stdout)
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = proc
        out = await DockerClient().run_cmd(["docker", "exec", "c", "npm", "ci"], max_bytes=4096, keep_tail=True)
    assert out.endswith("added 1450 packages"), "the end of the log is returned"
    assert len(out) < 4096 + 100, f"kept {len(out)} characters of a log whose tail is 4096 bytes"
    assert not proc.killed, "a log is read to its end, not stopped"
    assert stdout.handed == len(log) and -1 not in stdout.requests, "the log is drained in chunks"


@pytest.mark.asyncio
async def test_keep_tail_failure_quotes_the_end_of_the_log():
    """`install 2>&1` fails with an empty stderr: the error quotes the end of the log, and only that."""
    _run_cmd_takes_a_cap()
    log = b"npm warn deprecated\n" * 400_000 + b"npm error engine mismatch\n"
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = _Proc(_Pipe(data=log), returncode=1)
        with pytest.raises(RuntimeError) as caught:
            await DockerClient().run_cmd(["docker", "exec", "c", "npm", "ci"], max_bytes=4096, keep_tail=True)
    message = str(caught.value)
    assert "npm error engine mismatch" in message and "stdout tail" in message
    assert len(message) < 2200, f"the error carried {len(message)} characters"


@pytest.mark.asyncio
async def test_a_git_step_that_floods_its_output_is_stopped_at_the_default_cap():
    """Every embry0 git step in a sandbox goes through sandbox_git.run, which names no cap: the default holds there."""
    from embry0.execution import sandbox_git

    default = _bounded("DEFAULT_MAX_OUTPUT_BYTES")
    OutputTooLargeError = _too_large_error()
    stdout = _Pipe(total=default + 1024 * 1024)
    proc = _Proc(stdout)
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = proc
        with pytest.raises(OutputTooLargeError):
            await sandbox_git.run(
                DockerClient(),
                "sandbox-1",
                sandbox_git.command("status", "--porcelain"),
                step=sandbox_git.GitStep.SALVAGE_COMMIT,
                timeout=15,
            )
    assert stdout.handed <= default + 1, f"read {stdout.handed} bytes; the default cap is {default}"
    assert proc.killed, "the git step is stopped"


@pytest.mark.asyncio
async def test_a_git_listing_reads_past_the_default_cap_up_to_its_own():
    """A change touching 90,000 files lists past the 4 MiB default: a git listing names GIT_LISTING_MAX_BYTES."""
    from embry0.execution import sandbox_git

    default = _bounded("DEFAULT_MAX_OUTPUT_BYTES")
    cap = getattr(sandbox_git, "GIT_LISTING_MAX_BYTES", None)
    assert isinstance(cap, int) and cap > default, "sandbox_git must define GIT_LISTING_MAX_BYTES above the default"
    listing = b"".join(b"apps/web/src/components/generated/file_%06d.tsx\n" % i for i in range(90_000))
    assert len(listing) > default
    argv = sandbox_git.command("diff", "--name-only", "origin/main...HEAD")
    step = sandbox_git.GitStep.CHANGED_FILES
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = _Proc(_Pipe(data=listing))
        out = await sandbox_git.run(DockerClient(), "sandbox-1", argv, step=step, max_bytes=cap)
    assert len(out.splitlines()) == 90_000, "the whole listing is read"
    stdout = _Pipe(total=cap + 1024 * 1024)
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = _Proc(stdout)
        with pytest.raises(_too_large_error()):
            await sandbox_git.run(DockerClient(), "sandbox-1", argv, step=step, max_bytes=cap)
    assert stdout.handed <= cap + 1, f"read {stdout.handed} bytes; the listing cap is {cap}"


@pytest.mark.asyncio
async def test_exec_with_stdin_reads_its_output_in_chunks_and_keeps_little(tmp_path: Path, monkeypatch):
    """The write direction's output is not used: it is drained in chunks, never collected whole."""
    killed_groups: list[int] = []
    monkeypatch.setattr("os.killpg", lambda pgid, sig: killed_groups.append(pgid))
    payload = tmp_path / "ctx.tar"
    payload.write_bytes(b"x")
    stdout = _Pipe(total=8 * 1024 * 1024)
    stderr = _Pipe(data=b"tar: start\n" + b"w" * (1024 * 1024) + b"\ntar: the real cause\n")
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = _Proc(stdout, stderr, returncode=2)
        with pytest.raises(RuntimeError) as caught:
            await DockerClient().exec_with_stdin("sandbox-1", ["tar", "-x"], str(payload))
    assert -1 not in stdout.requests and -1 not in stderr.requests, "the exec's output must be read in chunks"
    assert stdout.handed == 8 * 1024 * 1024, "stdout is drained to its end"
    message = str(caught.value)
    assert "tar: the real cause" in message and len(message) < 82 * 1024, f"{len(message)} characters"
    assert killed_groups == [], "a command that exits on its own is not killed"


@pytest.mark.asyncio
async def test_the_compose_service_listing_is_capped(monkeypatch):
    """The live-log tabs' service listing goes through the same reader, with the daemon-listing cap."""
    from embry0.execution.runtime import DockerRuntime

    cap = _bounded("DAEMON_LISTING_MAX_BYTES")
    OutputTooLargeError = _too_large_error()
    killed_groups: list[int] = []
    monkeypatch.setattr("os.killpg", lambda pgid, sig: killed_groups.append(pgid))
    stdout = _Pipe(total=cap + 1024 * 1024, fill=b"s")
    proc = _Proc(stdout)
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = proc
        with pytest.raises(OutputTooLargeError):
            await DockerRuntime(DockerClient()).compose_services("qa_job-1")
    assert stdout.handed <= cap + 1, f"read {stdout.handed} bytes; the cap is {cap}"
    assert killed_groups == [proc.pid], "the listing's whole process group is killed"


@pytest.mark.asyncio
async def test_run_cmd_failure():
    """run_cmd raises on non-zero exit."""
    client = DockerClient()
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = _Proc(_Pipe(), _Pipe(data=b"Error: something failed\n"), returncode=1)

        with pytest.raises(RuntimeError, match="something failed"):
            await client.run_cmd(["docker", "ps"])


@pytest.mark.asyncio
async def test_run_cmd_failure_log_includes_caller_context():
    """Cleanup passes container + phase so a daemon outage is correlatable
    without inventing a job or trace id. Success and omitted context stay unchanged."""
    from structlog.testing import capture_logs

    client = DockerClient()
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = _Proc(
            _Pipe(), _Pipe(data=b"Cannot connect to the Docker daemon at tcp://dind:2376\n"), returncode=1
        )

        with capture_logs() as logs, pytest.raises(RuntimeError):
            await client.run_cmd(
                ["docker", "rm", "-f", "git-proxy"],
                log_context={"container": "git-proxy", "phase": "stop", "attempt": 1},
            )

    failed = [entry for entry in logs if entry["event"] == "docker_cmd_failed"]
    assert len(failed) == 1
    assert failed[0]["container"] == "git-proxy"
    assert failed[0]["phase"] == "stop"
    assert failed[0]["attempt"] == 1
    assert "trace_id" not in failed[0]
    assert failed[0]["returncode"] == 1


@pytest.mark.asyncio
async def test_run_cmd_kills_subprocess_on_timeout(monkeypatch):
    """When wait_for times out, the subprocess must be killed (not left
    running as a zombie that holds Docker daemon slots)."""
    killed = False

    class _FakeProc(_Proc):
        def __init__(self) -> None:
            # Prints nothing and never exits: longer than the timeout.
            super().__init__(_Pipe(hang=True), _Pipe(hang=True))

        def kill(self) -> None:
            nonlocal killed
            killed = True
            super().kill()

    async def _fake_create(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)

    client = DockerClient(docker_host="tcp://fake", tls_verify=False, cert_path="")

    with pytest.raises(TimeoutError):
        await client.run_cmd(["docker", "ps"], timeout=1)

    assert killed, "subprocess was not killed on timeout"


def test_run_cmd_includes_memory_swap_and_nofile():
    cmd = DockerClient().build_run_cmd(image="img:latest", name="sandbox-test")
    assert "--memory-swap=8g" in cmd
    assert "--ulimit=nofile=4096:8192" in cmd


def test_proxy_run_cmd_uses_env_file(tmp_path):
    import os

    cmd, env_file = DockerClient().build_run_proxy_cmd(
        name="git-proxy",
        image="embry0-proxy:latest",
        network="sandbox-restricted",
        env={"PROXY_TYPE": "git", "GITHUB_TOKEN": "x", "PROXY_ADMIN_TOKEN": "y"},
    )
    try:
        assert "--env-file" in cmd
        assert env_file in cmd
        assert os.path.exists(env_file)
        st = os.stat(env_file)
        assert oct(st.st_mode & 0o777) == "0o600"
        with open(env_file) as f:
            content = f.read()
        assert "PROXY_TYPE=git" in content
        assert "GITHUB_TOKEN=x" in content
        assert "PROXY_ADMIN_TOKEN=y" in content
        # Env values must not appear in the argv
        assert all("GITHUB_TOKEN=" not in arg for arg in cmd)
    finally:
        os.unlink(env_file)


def test_proxy_run_cmd_registry_qualified_image_forces_pull():
    """Proxies must force a fresh pull for registry-qualified images, exactly
    like sandboxes (test_build_run_cmd_registry_qualified_image_forces_pull).
    Without it DinD serves its cached `latest` after the host rebuilds and
    init-push-images refreshes the registry — the stale-image failure mode
    that silently ran a stale git-proxy for two months (found 2026-07-06
    during an access smoke test: per-owner enroll tokens were ignored by
    the old handler still running from a 2026-05-04 cache)."""
    import os

    cmd, env_file = DockerClient().build_run_proxy_cmd(
        name="git-proxy",
        image="registry:5000/embry0-proxy:latest",
        network="sandbox-restricted",
        env={"PROXY_TYPE": "git"},
    )
    try:
        assert "--pull=always" in cmd
        assert cmd.index("--pull=always") < cmd.index("registry:5000/embry0-proxy:latest")
    finally:
        os.unlink(env_file)


def test_proxy_run_cmd_unqualified_image_omits_pull():
    """A bare local-only image name must not add --pull=always (pull would fail)."""
    import os

    cmd, env_file = DockerClient().build_run_proxy_cmd(
        name="git-proxy",
        image="embry0-proxy:latest",
        network="sandbox-restricted",
        env={"PROXY_TYPE": "git"},
    )
    try:
        assert "--pull=always" not in cmd
    finally:
        os.unlink(env_file)


@pytest.mark.asyncio
async def test_run_cmd_failure_surfaces_stdout_tail_when_stderr_empty(monkeypatch):
    """RAV-1400: `cmd 2>&1` failures used to raise "Docker command failed: "
    with no cause — the output was on stdout, which the error discarded."""
    client = DockerClient()

    async def fake_exec(*cmd, **kw):
        return _Proc(_Pipe(data=b"npm error code EUSAGE\nnpm error engine mismatch\n"), returncode=1)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    with pytest.raises(RuntimeError) as exc:
        await client.run_cmd(["docker", "exec", "c1", "bash", "-c", "false 2>&1"])
    assert "npm error engine mismatch" in str(exc.value)
    assert "stdout tail" in str(exc.value)


async def test_stream_exec_reads_lines_past_asyncio_default_limit(docker: DockerClient):
    """A stdout line bigger than asyncio's 64 KiB default must still stream.

    The sandbox runner prints ``final_result`` as ONE JSON line carrying the
    full ``messages`` transcript; a watcher analysis job's line runs to
    hundreds of KB and used to die with "Separator is found, but chunk is
    longer than limit" once the agent had already finished.
    """
    line_len = 1024 * 1024
    script = f"import sys; sys.stdout.write('x' * {line_len} + '\\n')"
    with patch.object(docker, "build_exec_cmd", return_value=[sys.executable, "-c", script]):
        proc = await docker.stream_exec("sandbox-job-123", ["ignored"])
    assert proc.stdout is not None
    lines = [raw async for raw in proc.stdout]
    await proc.wait()
    assert [len(raw.rstrip(b"\n")) for raw in lines] == [line_len]


async def test_stream_exec_feeds_stdin_data_past_the_argv_limit(docker: DockerClient):
    """#304: payloads too big for one argv string (128 KiB) go in on stdin;
    the child sees all of it followed by EOF."""
    payload = b"x" * (512 * 1024)
    script = "import sys; print(len(sys.stdin.buffer.read()))"
    with patch.object(docker, "build_exec_cmd", return_value=[sys.executable, "-c", script]) as build:
        proc = await docker.stream_exec("sandbox-job-123", ["ignored"], stdin_data=payload)
    assert proc.stdout is not None
    lines = [raw async for raw in proc.stdout]
    await proc.wait()
    assert lines == [f"{len(payload)}\n".encode()]
    assert build.call_args.kwargs["interactive"] is True  # docker exec -i, or stdin is not attached


async def test_stream_exec_without_stdin_data_stays_non_interactive(docker: DockerClient):
    with patch.object(docker, "build_exec_cmd", return_value=[sys.executable, "-c", "pass"]) as build:
        proc = await docker.stream_exec("sandbox-job-123", ["ignored"])
    await proc.wait()
    assert build.call_args.kwargs["interactive"] is False
    assert proc.stdin is None


async def test_stream_exec_survives_a_child_that_exits_before_reading_stdin(docker: DockerClient):
    """A runner that dies at import time closes the pipe early; the caller
    must still get the process back to read its exit code and stderr."""
    script = "import sys; sys.stdin.close(); sys.exit(3)"
    with patch.object(docker, "build_exec_cmd", return_value=[sys.executable, "-c", script]):
        proc = await docker.stream_exec("sandbox-job-123", ["ignored"], stdin_data=b"x" * (4 * 1024 * 1024))
    assert await proc.wait() == 3


async def test_stream_exec_fails_fast_when_nothing_reads_stdin(docker: DockerClient, monkeypatch):
    """The runner's idle/timeout watchdogs start only after stream_exec
    returns, so a wedged exec must not block the stdin hand-off forever."""
    monkeypatch.setattr("embry0.execution.docker_client._STDIN_WRITE_TIMEOUT_S", 0.2)
    script = "import time; time.sleep(30)"
    from embry0.execution.docker_client import ExecStdinTimeoutError

    spawned = []
    real_exec = asyncio.create_subprocess_exec

    async def _spy(*args, **kwargs):
        spawned.append(await real_exec(*args, **kwargs))
        return spawned[-1]

    monkeypatch.setattr("asyncio.create_subprocess_exec", _spy)
    with (
        patch.object(docker, "build_exec_cmd", return_value=[sys.executable, "-c", script]),
        pytest.raises(ExecStdinTimeoutError, match="did not read"),
    ):
        await docker.stream_exec("sandbox-job-123", ["ignored"], stdin_data=b"x" * (4 * 1024 * 1024))
    assert spawned[0].returncode is not None  # killed AND reaped, no zombie docker client


def test_scrub_cmd_redacts_the_runner_config_value():
    """The stale-image fallback (#304) puts the config back on argv; its
    prompt and settings must not reach the debug/timeout/failure logs."""
    from embry0.execution.docker_client import _scrub_cmd_for_log

    cmd = [
        "docker",
        "exec",
        "c",
        "python",
        "-m",
        "embry0.sandbox.runner",
        "--config",
        '{"prompt": "p"}',
        "--session-id",
        "s",
    ]
    assert _scrub_cmd_for_log(cmd) == [*cmd[:7], "***REDACTED***", "--session-id", "s"]


def test_inspect_running_cmd_prints_the_running_flag() -> None:
    """SandboxManager.reenroll reads this as exactly ``true`` or ``false``."""
    client = DockerClient(docker_host="tcp://fake", tls_verify=False, cert_path="")
    cmd = client.build_inspect_running_cmd("sandbox-job-1")
    assert cmd[-4:] == ["inspect", "--format", "{{.State.Running}}", "sandbox-job-1"], cmd


@pytest.mark.asyncio
async def test_an_image_the_daemon_lacks_is_an_answer_not_an_error():
    """#655: ``image_exists`` asks whether the daemon holds an image; "No such
    image" is its "no". Logged at error, every deploy that pins new digests
    raised a false self-healing signal before the image was pulled."""
    from structlog.testing import capture_logs

    from embry0.execution.runtime import DockerRuntime

    runtime = DockerRuntime(DockerClient())
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = _Proc(
            _Pipe(), _Pipe(data=b"Error response from daemon: No such image: x\n"), returncode=1
        )

        with capture_logs() as logs:
            assert await runtime.image_exists("registry:5000/embry0-proxy:latest") is False

    errors = [entry for entry in logs if entry["log_level"] == "error"]
    assert not errors, f"an absent image must not log at error: {errors}"


def test_a_timed_out_exec_with_stdin_is_killed_with_its_children_before_it_raises(tmp_path: Path) -> None:
    """#334: the timed-out exec was killed but never reaped, so it and its children were left to the garbage
    collector, and once the event loop had closed its transport raised from ``__del__``."""
    from tests.fixtures.hung_process import hung_script, run_until_it_raises

    pidfile = tmp_path / "grandchild.pid"
    payload = tmp_path / "payload.tar"
    payload.write_bytes(b"x")
    client = DockerClient()
    client.build_exec_cmd = lambda *_args, **_kwargs: ["sh", "-c", hung_script(pidfile)]  # type: ignore[method-assign]
    left = run_until_it_raises(
        lambda: client.exec_with_stdin("sandbox-1", ["tar", "-x"], str(payload), timeout=0.5),  # type: ignore[arg-type]
        RuntimeError,
        pidfile,
    )
    assert left.seconds < 3, f"the timed-out exec raised only after {left.seconds:.1f}s, not promptly"
    assert not left.grandchild_alive, "the exec's child outlived its timeout"
    assert left.unraisable == [], f"the killed exec was left to the garbage collector: {left.unraisable}"
