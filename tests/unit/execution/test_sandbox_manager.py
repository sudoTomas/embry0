from unittest.mock import AsyncMock, MagicMock

import pytest

from athanor.execution.sandbox_manager import SandboxManager


def _make_proxy_manager(token: str = "tok-abc123") -> MagicMock:
    """Return a mock ProxyManager whose enroll_sandbox returns a fake token."""
    proxy_mgr = MagicMock()
    proxy_mgr.enroll_sandbox = AsyncMock(return_value=token)
    proxy_mgr.unenroll_sandbox = AsyncMock()
    return proxy_mgr


@pytest.fixture
def manager() -> SandboxManager:
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="container-abc123")
    docker.build_run_cmd = MagicMock(return_value=["docker", "run", "..."])
    docker.build_stop_cmd = MagicMock(return_value=["docker", "stop", "..."])
    docker.build_rm_cmd = MagicMock(return_value=["docker", "rm", "..."])
    docker.build_network_cmd = MagicMock(return_value=["docker", "network", "..."])
    return SandboxManager(docker=docker, proxy_manager=_make_proxy_manager())


@pytest.mark.asyncio
async def test_create_sandbox(manager: SandboxManager):
    container_id, sandbox_token = await manager.create(job_id="job-abc123")
    assert container_id == "container-abc123"
    assert sandbox_token == "tok-abc123"
    manager._docker.build_run_cmd.assert_called_once()
    call_kwargs = manager._docker.build_run_cmd.call_args
    assert call_kwargs.kwargs["name"] == "sandbox-job-abc123"


@pytest.mark.asyncio
async def test_destroy_sandbox(manager: SandboxManager):
    await manager.destroy("sandbox-job-abc123")
    manager._proxy_manager.unenroll_sandbox.assert_awaited_once_with("sandbox-job-abc123")
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
    """Caller-provided env (like ATHANOR_GIT_PROXY_URL) must reach the container."""
    await manager.create(
        job_id="job-xyz",
        env={"ATHANOR_GIT_PROXY_URL": "http://git-proxy:9101"},
    )

    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    env_passed = kwargs.get("env", {}) or {}

    assert env_passed.get("ATHANOR_GIT_PROXY_URL") == "http://git-proxy:9101"


@pytest.mark.asyncio
async def test_create_with_custom_profile(manager: SandboxManager):
    profile = {
        "base_image": "athanor-sandbox-java:17",
        "memory": "12g",
        "cpus": "6",
        "pids_limit": 512,
    }
    await manager.create(job_id="job-y", profile=profile)
    kwargs = manager._docker.build_run_cmd.call_args.kwargs
    assert kwargs["image"] == "athanor-sandbox-java:17"
    assert kwargs["memory"] == "12g"
    assert kwargs["cpus"] == "6"
    assert kwargs["pids_limit"] == 512


@pytest.mark.asyncio
async def test_create_rolls_back_on_enrollment_failure():
    """If enroll_sandbox fails, the container is removed and SandboxInitError raised."""
    from athanor.execution.sandbox_manager import SandboxInitError

    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="container-fail")
    docker.build_run_cmd = MagicMock(return_value=["docker", "run"])
    docker.build_rm_cmd = MagicMock(return_value=["docker", "rm", "sandbox-job-fail"])

    proxy_mgr = MagicMock()
    proxy_mgr.enroll_sandbox = AsyncMock(side_effect=RuntimeError("proxy unreachable"))
    proxy_mgr.unenroll_sandbox = AsyncMock()

    mgr = SandboxManager(docker=docker, proxy_manager=proxy_mgr)

    with pytest.raises(SandboxInitError, match="enrollment failed"):
        await mgr.create(job_id="job-fail")

    docker.build_rm_cmd.assert_called()  # rollback was attempted


@pytest.mark.asyncio
async def test_create_rolls_back_partial_enrollment_on_enroll_failure():
    """On enroll_sandbox failure, unenroll_sandbox is called before container rm
    to clean up any partial proxy enrollments that succeeded before the failure.
    """
    from athanor.execution.sandbox_manager import SandboxInitError

    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="container-partial")
    docker.build_run_cmd = MagicMock(return_value=["docker", "run"])
    docker.build_rm_cmd = MagicMock(return_value=["docker", "rm", "sandbox-job-partial"])

    proxy_mgr = MagicMock()
    proxy_mgr.enroll_sandbox = AsyncMock(side_effect=RuntimeError("github-proxy unreachable"))
    proxy_mgr.unenroll_sandbox = AsyncMock()

    mgr = SandboxManager(docker=docker, proxy_manager=proxy_mgr)

    with pytest.raises(SandboxInitError, match="enrollment failed"):
        await mgr.create(job_id="job-partial")

    # unenroll_sandbox must have been called exactly once with the container_id
    assert proxy_mgr.unenroll_sandbox.call_count == 1
    assert proxy_mgr.unenroll_sandbox.call_args_list[0].args[0] == "container-partial"
