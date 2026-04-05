from unittest.mock import AsyncMock, patch

import pytest

from legion.execution.docker_client import DockerClient


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
        image="legion-sandbox:latest",
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
    assert "legion-sandbox:latest" in cmd
    assert cmd[-1] == "infinity"  # sleep infinity


def test_build_run_cmd_volumes(docker: DockerClient):
    """Volumes are passed as -v flags, appearing before image."""
    cmd = docker.build_run_cmd(
        image="legion-sandbox:latest",
        name="sandbox-job-123",
        volumes=["/host/path:/container/path:ro", "/data:/data"],
    )
    # Both volumes appear as -v flag pairs
    assert "-v" in cmd
    v_pairs = [cmd[i + 1] for i, t in enumerate(cmd) if t == "-v"]
    assert "/host/path:/container/path:ro" in v_pairs
    assert "/data:/data" in v_pairs
    # Image must come after all -v flags
    image_idx = cmd.index("legion-sandbox:latest")
    for v in v_pairs:
        assert cmd.index(v) < image_idx


def test_build_run_cmd_no_volumes(docker: DockerClient):
    """Without volumes parameter, no -v flags appear."""
    cmd = docker.build_run_cmd(image="legion-sandbox:latest", name="sandbox-job-123")
    assert "-v" not in cmd


def test_build_exec_cmd(docker: DockerClient):
    """Exec command targets container by name."""
    cmd = docker.build_exec_cmd(
        container="sandbox-job-123",
        command=["python", "-m", "legion.sandbox.runner", "--config", "{}"],
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
