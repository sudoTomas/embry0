import asyncio
import sys
from pathlib import Path
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
        process = AsyncMock()
        process.communicate = AsyncMock(return_value=(b"container-id\n", b""))
        process.returncode = 0
        mock_proc.return_value = process

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
    """A process pipe: hands out ``total`` bytes (``fill``) in whatever sizes are asked for, then EOF."""

    def __init__(self, total: int = 0, data: bytes = b"", fill: bytes = b"x") -> None:
        self._data = data
        self._total = total if not data else len(data)
        self._fill = fill
        self.handed = 0

    async def read(self, n: int = -1) -> bytes:
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
async def test_run_cmd_bytes_keeps_only_the_head_of_a_flooded_stderr():
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = _Proc(_Pipe(), _Pipe(total=4 * 1024 * 1024, fill=b"e"), returncode=1)
        with pytest.raises(RuntimeError) as caught:
            await DockerClient().run_cmd_bytes(["docker", "x"])
    assert len(str(caught.value)) < 70 * 1024, "stderr is kept to its first 64 KiB"


@pytest.mark.asyncio
async def test_run_cmd_failure():
    """run_cmd raises on non-zero exit."""
    client = DockerClient()
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        process = AsyncMock()
        process.communicate = AsyncMock(return_value=(b"", b"Error: something failed\n"))
        process.returncode = 1
        mock_proc.return_value = process

        with pytest.raises(RuntimeError, match="something failed"):
            await client.run_cmd(["docker", "ps"])


@pytest.mark.asyncio
async def test_run_cmd_failure_log_includes_caller_context():
    """Cleanup passes container + phase so a daemon outage is correlatable
    without inventing a job or trace id. Success and omitted context stay unchanged."""
    from structlog.testing import capture_logs

    client = DockerClient()
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
        process = AsyncMock()
        process.communicate = AsyncMock(return_value=(b"", b"Cannot connect to the Docker daemon at tcp://dind:2376\n"))
        process.returncode = 1
        mock_proc.return_value = process

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

    class _FakeProc:
        def __init__(self) -> None:
            self.pid = 123456
            self.returncode = -9

        async def communicate(self):
            await asyncio.sleep(30)  # longer than timeout — wait_for will fire

        def kill(self) -> None:
            nonlocal killed
            killed = True

        async def wait(self) -> int:
            return -9

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

    class _Proc:
        returncode = 1
        pid = 4242

        async def communicate(self):
            return (b"npm error code EUSAGE\nnpm error engine mismatch\n", b"")

    async def fake_exec(*cmd, **kw):
        return _Proc()

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
        process = AsyncMock()
        process.communicate = AsyncMock(return_value=(b"", b"Error response from daemon: No such image: x\n"))
        process.returncode = 1
        mock_proc.return_value = process

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
