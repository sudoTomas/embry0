"""Tests for SandboxImageManager — proxy image presence check."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from athanor.execution.image_manager import SandboxImageManager


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
