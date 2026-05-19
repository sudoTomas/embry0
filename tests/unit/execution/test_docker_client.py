import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from athanor.execution.docker_client import DockerClient


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
        image="athanor-sandbox:latest",
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
    assert "athanor-sandbox:latest" in cmd
    assert cmd[-1] == "infinity"  # sleep infinity


def test_build_run_cmd_volumes(docker: DockerClient):
    """Volumes are passed as -v flags, appearing before image."""
    cmd = docker.build_run_cmd(
        image="athanor-sandbox:latest",
        name="sandbox-job-123",
        volumes=["/host/path:/container/path:ro", "/data:/data"],
    )
    # Both volumes appear as -v flag pairs
    assert "-v" in cmd
    v_pairs = [cmd[i + 1] for i, t in enumerate(cmd) if t == "-v"]
    assert "/host/path:/container/path:ro" in v_pairs
    assert "/data:/data" in v_pairs
    # Image must come after all -v flags
    image_idx = cmd.index("athanor-sandbox:latest")
    for v in v_pairs:
        assert cmd.index(v) < image_idx


def test_build_run_cmd_no_volumes(docker: DockerClient):
    """Without volumes parameter, no -v flags appear."""
    cmd = docker.build_run_cmd(image="athanor-sandbox:latest", name="sandbox-job-123")
    assert "-v" not in cmd


def test_build_run_cmd_extra_hosts(docker: DockerClient):
    """extra_hosts maps to --add-host=name:ip flags."""
    cmd = docker.build_run_cmd(
        image="athanor-sandbox:latest",
        name="sandbox-1",
        extra_hosts={"dind": "172.24.0.3", "minio-proxy": "172.24.0.7"},
    )
    assert "--add-host=dind:172.24.0.3" in cmd
    assert "--add-host=minio-proxy:172.24.0.7" in cmd


def test_build_run_cmd_no_extra_hosts(docker: DockerClient):
    """Without extra_hosts, no --add-host flag is emitted."""
    cmd = docker.build_run_cmd(image="athanor-sandbox:latest", name="sandbox-1")
    assert not any(t.startswith("--add-host") for t in cmd)


def test_build_run_cmd_unqualified_image_omits_pull(docker: DockerClient):
    """A bare image name (no registry segment) must NOT add --pull=always —
    the image only exists locally and a pull would fail."""
    cmd = docker.build_run_cmd(image="athanor-sandbox:latest", name="sandbox-1")
    assert "--pull=always" not in cmd


def test_build_run_cmd_registry_qualified_image_forces_pull(docker: DockerClient):
    """When the image is qualified against the in-stack registry (host
    contains `:` for the port), every launch must force a fresh pull. This
    prevents DinD from serving a stale `latest` after the host rebuilds
    and init-push-images refreshes the registry — the silent failure
    mode that masked the QA pipeline's runner-import crash on 2026-05-06."""
    cmd = docker.build_run_cmd(image="registry:5000/athanor-sandbox:latest", name="sandbox-1")
    assert "--pull=always" in cmd
    # And it must precede the image arg / "run -d" must still be there.
    assert "run" in cmd
    assert cmd.index("--pull=always") < cmd.index("registry:5000/athanor-sandbox:latest")


def test_build_run_cmd_dotted_registry_host_forces_pull(docker: DockerClient):
    """A registry hostname with a `.` (e.g. ghcr.io/...) is also qualified."""
    cmd = docker.build_run_cmd(image="ghcr.io/client-project/athanor-sandbox:latest", name="sandbox-1")
    assert "--pull=always" in cmd


def test_build_run_cmd_localhost_registry_forces_pull(docker: DockerClient):
    """A `localhost/...` qualified image (Docker's special form) also pulls."""
    cmd = docker.build_run_cmd(image="localhost/athanor-sandbox:latest", name="sandbox-1")
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
    from athanor.execution.docker_client import _scrub_cmd_for_log

    cmd = [
        "docker",
        "run",
        "-d",
        "-e",
        "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-secret-value",
        "-e",
        "QA_JOB_ID=job-1",
        "registry:5000/athanor-sandbox:latest",
    ]
    scrubbed = _scrub_cmd_for_log(cmd)
    assert "CLAUDE_CODE_OAUTH_TOKEN=***REDACTED***" in scrubbed
    assert "sk-ant-oat01-secret-value" not in " ".join(scrubbed)
    # Non-secret env stays intact.
    assert "QA_JOB_ID=job-1" in scrubbed
    # Length and order preserved (consumers may index into the array).
    assert len(scrubbed) == len(cmd)
    assert scrubbed[4] == "CLAUDE_CODE_OAUTH_TOKEN=***REDACTED***"


def test_scrub_cmd_redacts_known_secret_keys():
    from athanor.execution.docker_client import _scrub_cmd_for_log

    cmd = [
        "docker",
        "run",
        "-e",
        "ANTHROPIC_API_KEY=sk-ant-api...",
        "-e",
        "GITHUB_TOKEN=ghp_...",
        "-e",
        "ATHANOR_SANDBOX_TOKEN=tok-...",
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


def test_scrub_cmd_leaves_unrelated_kv_alone():
    from athanor.execution.docker_client import _scrub_cmd_for_log

    cmd = ["docker", "exec", "-e", "FOO=bar", "-e", "DEBUG=true", "container", "true"]
    assert _scrub_cmd_for_log(cmd) == cmd


def test_scrub_cmd_handles_dangling_e_flag():
    """`-e` at the end with nothing after it: don't crash, don't redact spuriously."""
    from athanor.execution.docker_client import _scrub_cmd_for_log

    cmd = ["docker", "run", "img", "-e"]
    assert _scrub_cmd_for_log(cmd) == cmd


def test_scrub_cmd_handles_e_without_equals():
    """`-e KEY` (no value) — docker reads it from the host env. Pass through."""
    from athanor.execution.docker_client import _scrub_cmd_for_log

    cmd = ["docker", "run", "-e", "GITHUB_TOKEN", "img"]
    # No `=` in the value, so the partition fallback kicks in and we just
    # leave the token alone (docker would resolve it from env at run time).
    assert _scrub_cmd_for_log(cmd) == cmd


def test_build_exec_cmd(docker: DockerClient):
    """Exec command targets container by name."""
    cmd = docker.build_exec_cmd(
        container="sandbox-job-123",
        command=["python", "-m", "athanor.sandbox.runner", "--config", "{}"],
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
        image="athanor-proxy:latest",
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
