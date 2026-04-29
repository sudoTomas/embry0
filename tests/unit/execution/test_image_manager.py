"""Tests for SandboxImageManager — proxy image presence check + expire paused jobs routing."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from athanor.execution.image_manager import ContainerReaper, SandboxImageManager


@pytest.mark.asyncio
async def test_check_proxy_image_present_returns_true_when_image_exists():
    docker = MagicMock()
    docker._build_base_cmd = MagicMock(return_value=["docker"])
    docker.run_cmd = AsyncMock(return_value=b"some json")
    mgr = SandboxImageManager(docker)
    assert await mgr.check_proxy_image_present() is True


@pytest.mark.asyncio
async def test_check_proxy_image_present_returns_false_when_image_missing():
    docker = MagicMock()
    docker._build_base_cmd = MagicMock(return_value=["docker"])
    docker.run_cmd = AsyncMock(side_effect=RuntimeError("No such image"))
    mgr = SandboxImageManager(docker)
    assert await mgr.check_proxy_image_present() is False


@pytest.mark.asyncio
async def test_check_proxy_image_present_uses_correct_image_tag():
    docker = MagicMock()
    docker._build_base_cmd = MagicMock(return_value=["docker"])
    docker.run_cmd = AsyncMock(return_value=b"")
    mgr = SandboxImageManager(docker)
    await mgr.check_proxy_image_present()
    # The cmd issued should have included athanor-proxy:latest
    called_cmd = docker.run_cmd.call_args[0][0]
    assert "athanor-proxy:latest" in called_cmd
    assert "image" in called_cmd
    assert "inspect" in called_cmd


@pytest.mark.asyncio
async def test_expire_paused_jobs_uses_jobs_repo():
    """_expire_paused_jobs must call jobs_repo.update, not raw db.execute for status."""
    mock_db = AsyncMock()
    mock_db.fetch = AsyncMock(return_value=[{"job_id": "job-abc"}])
    # fetchrow returns None (no associated issue)
    mock_db.fetchrow = AsyncMock(return_value=None)

    mock_jobs_repo = AsyncMock()
    mock_jobs_repo.update = AsyncMock()

    mock_docker = MagicMock()
    mock_docker.build_stop_cmd = MagicMock(return_value=["docker", "stop"])
    mock_docker.build_rm_cmd = MagicMock(return_value=["docker", "rm"])
    mock_docker.run_cmd = AsyncMock(side_effect=RuntimeError("no container"))

    reaper = ContainerReaper(
        mock_docker,
        db=mock_db,
        jobs_repo=mock_jobs_repo,
        paused_ttl_hours=0,
    )

    await reaper._expire_paused_jobs()

    # jobs_repo.update must have been called with status="expired"
    mock_jobs_repo.update.assert_awaited_once_with("job-abc", status="expired")

    # Raw db.execute must NOT have been called with a SET status statement
    for c in mock_db.execute.call_args_list:
        assert "SET status" not in str(c), f"raw execute should not set status: {c}"


@pytest.mark.asyncio
async def test_expire_paused_jobs_fallback_without_jobs_repo():
    """When jobs_repo is None, _expire_paused_jobs falls back to raw db.execute."""
    mock_db = AsyncMock()
    mock_db.fetch = AsyncMock(return_value=[{"job_id": "job-xyz"}])
    mock_db.execute = AsyncMock(return_value="UPDATE 1")
    mock_db.fetchrow = AsyncMock(return_value=None)

    mock_docker = MagicMock()
    mock_docker.build_stop_cmd = MagicMock(return_value=["docker", "stop"])
    mock_docker.build_rm_cmd = MagicMock(return_value=["docker", "rm"])
    mock_docker.run_cmd = AsyncMock(side_effect=RuntimeError("no container"))

    reaper = ContainerReaper(
        mock_docker,
        db=mock_db,
        jobs_repo=None,
        paused_ttl_hours=0,
    )

    await reaper._expire_paused_jobs()

    # Confirm raw execute was called with the status update
    executed_calls = [str(c) for c in mock_db.execute.call_args_list]
    assert any("expired" in c for c in executed_calls), (
        f"Expected a raw execute with 'expired' when jobs_repo is None. Calls: {executed_calls}"
    )
