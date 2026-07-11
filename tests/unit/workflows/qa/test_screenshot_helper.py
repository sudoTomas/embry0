"""take_diagnostic_screenshot: docker exec into the sandbox running a Playwright Node script."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.workflows.qa.screenshot import take_diagnostic_screenshot


@pytest.mark.asyncio
async def test_runs_node_script_in_sandbox():
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="")
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])
    docker.copy_bytes_from = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n...")

    blob = await take_diagnostic_screenshot(
        docker=docker,
        container_id="C",
        frontend_url="http://macrolab-frontend:3000",
        sandbox_path="/tmp/.embry0-boot-screenshot.png",
    )
    assert blob.startswith(b"\x89PNG")
    docker.run_cmd.assert_awaited_once()
    cmd = docker.run_cmd.await_args[0][0]
    joined = " ".join(cmd)
    assert "node" in joined or "playwright" in joined
    assert "macrolab-frontend" in joined
    assert "/tmp/.embry0-boot-screenshot.png" in joined
    docker.copy_bytes_from.assert_awaited_once_with("C", "/tmp/.embry0-boot-screenshot.png")


@pytest.mark.asyncio
async def test_returns_none_on_run_failure():
    """Best-effort: docker exec failure returns None, doesn't raise."""
    docker = MagicMock()
    docker.run_cmd = AsyncMock(side_effect=RuntimeError("playwright crash"))
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])
    docker.copy_bytes_from = AsyncMock()

    blob = await take_diagnostic_screenshot(
        docker=docker,
        container_id="C",
        frontend_url="http://macrolab-frontend:3000",
        sandbox_path="/tmp/.embry0-boot-screenshot.png",
    )
    assert blob is None
    docker.copy_bytes_from.assert_not_awaited()


@pytest.mark.asyncio
async def test_returns_none_on_copy_failure():
    """If the script ran but the file isn't there to copy out, return None."""
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="")
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])
    docker.copy_bytes_from = AsyncMock(side_effect=RuntimeError("no such file"))

    blob = await take_diagnostic_screenshot(
        docker=docker,
        container_id="C",
        frontend_url="http://macrolab-frontend:3000",
        sandbox_path="/tmp/.embry0-boot-screenshot.png",
    )
    assert blob is None
