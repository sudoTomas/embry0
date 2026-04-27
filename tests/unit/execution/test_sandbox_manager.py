from unittest.mock import AsyncMock, MagicMock

import pytest

from athanor.execution.sandbox_manager import SandboxManager


@pytest.fixture
def manager() -> SandboxManager:
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="container-abc123")
    docker.build_run_cmd = MagicMock(return_value=["docker", "run", "..."])
    docker.build_stop_cmd = MagicMock(return_value=["docker", "stop", "..."])
    docker.build_rm_cmd = MagicMock(return_value=["docker", "rm", "..."])
    docker.build_network_cmd = MagicMock(return_value=["docker", "network", "..."])
    return SandboxManager(docker=docker)


@pytest.mark.asyncio
async def test_create_sandbox(manager: SandboxManager):
    container_id = await manager.create(job_id="job-abc123")
    assert container_id == "container-abc123"
    manager._docker.build_run_cmd.assert_called_once()
    call_kwargs = manager._docker.build_run_cmd.call_args
    assert call_kwargs.kwargs["name"] == "sandbox-job-abc123"


@pytest.mark.asyncio
async def test_destroy_sandbox(manager: SandboxManager):
    await manager.destroy("sandbox-job-abc123")
    manager._docker.build_stop_cmd.assert_called_once()
    manager._docker.build_rm_cmd.assert_called_once()


@pytest.mark.asyncio
async def test_connect_network(manager: SandboxManager):
    await manager.connect_network("sandbox-job-abc123", "sandbox-internet")
    manager._docker.build_network_cmd.assert_called_with("connect", "sandbox-internet", "sandbox-job-abc123")


@pytest.mark.asyncio
async def test_disconnect_network(manager: SandboxManager):
    await manager.disconnect_network("sandbox-job-abc123", "sandbox-internet")
    manager._docker.build_network_cmd.assert_called_with("disconnect", "sandbox-internet", "sandbox-job-abc123")


@pytest.mark.asyncio
async def test_create_uses_sandbox_profile_defaults(manager: SandboxManager):
    await manager.create(job_id="job-x")
    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    assert kwargs["memory"] == "8g"
    assert kwargs["cpus"] == "4"
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["network"] == "sandbox-restricted"


@pytest.mark.asyncio
async def test_sandbox_manager_does_not_inject_github_token(manager: SandboxManager, monkeypatch):
    """SandboxManager must NOT read GITHUB_TOKEN from the orchestrator's env
    or inject it into the sandbox container. Git auth flows through the
    credential proxy instead (see Plan C Task 2)."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_would_leak_if_injected")

    await manager.create(job_id="job-xyz")

    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    env_passed = kwargs.get("env", {}) or {}

    assert "GITHUB_TOKEN" not in env_passed, (
        f"GITHUB_TOKEN must not be injected into the sandbox env. Found keys: {sorted(env_passed)}"
    )


@pytest.mark.asyncio
async def test_sandbox_manager_passes_through_caller_env(manager: SandboxManager):
    """Caller-provided env (like LEGION_GIT_PROXY_URL) must reach the container."""
    await manager.create(
        job_id="job-xyz",
        env={"LEGION_GIT_PROXY_URL": "http://host.docker.internal:9101"},
    )

    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    env_passed = kwargs.get("env", {}) or {}

    assert env_passed.get("LEGION_GIT_PROXY_URL") == "http://host.docker.internal:9101"


@pytest.mark.asyncio
async def test_create_with_custom_profile(manager: SandboxManager):
    profile = {
        "base_image": "legion-sandbox-java:17",
        "memory": "12g",
        "cpus": "6",
        "pids_limit": 512,
    }
    await manager.create(job_id="job-y", profile=profile)
    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    assert kwargs["image"] == "legion-sandbox-java:17"
    assert kwargs["memory"] == "12g"
    assert kwargs["cpus"] == "6"
    assert kwargs["pids_limit"] == 512
