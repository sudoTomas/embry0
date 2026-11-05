"""take_diagnostic_screenshot: docker exec into the sandbox running a Playwright Node script."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.workflows.qa.screenshot import take_diagnostic_screenshot


@pytest.mark.asyncio
async def test_runs_node_script_in_sandbox():
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="")
    docker.build_exec_cmd = MagicMock(
        side_effect=lambda cid, cmd, **kw: [
            "docker",
            "exec",
            *(["-u", kw["user"]] if kw.get("user") else []),
            cid,
            *cmd,
        ]
    )
    docker.run_cmd_bytes = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n...")
    docker.copy_bytes_from = AsyncMock(
        return_value=b"\x89PNG"
    )  # removed API; lets a pre-change tree reach an assertion

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
    docker.run_cmd_bytes.assert_awaited_once()
    read_cmd = docker.run_cmd_bytes.await_args.args[0]
    assert read_cmd[:4] == ["docker", "exec", "-u", "agent"] and "/tmp/.embry0-boot-screenshot.png" in read_cmd


@pytest.mark.asyncio
async def test_returns_none_on_run_failure():
    """Best-effort: docker exec failure returns None, doesn't raise."""
    docker = MagicMock()
    docker.run_cmd = AsyncMock(side_effect=RuntimeError("playwright crash"))
    docker.build_exec_cmd = MagicMock(
        side_effect=lambda cid, cmd, **kw: [
            "docker",
            "exec",
            *(["-u", kw["user"]] if kw.get("user") else []),
            cid,
            *cmd,
        ]
    )
    docker.run_cmd_bytes = AsyncMock()

    blob = await take_diagnostic_screenshot(
        docker=docker,
        container_id="C",
        frontend_url="http://macrolab-frontend:3000",
        sandbox_path="/tmp/.embry0-boot-screenshot.png",
    )
    assert blob is None
    docker.run_cmd_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_returns_none_on_copy_failure():
    """If the script ran but the file isn't there to copy out, return None."""
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="")
    docker.build_exec_cmd = MagicMock(
        side_effect=lambda cid, cmd, **kw: [
            "docker",
            "exec",
            *(["-u", kw["user"]] if kw.get("user") else []),
            cid,
            *cmd,
        ]
    )
    docker.run_cmd_bytes = AsyncMock(side_effect=RuntimeError("no such file"))

    blob = await take_diagnostic_screenshot(
        docker=docker,
        container_id="C",
        frontend_url="http://macrolab-frontend:3000",
        sandbox_path="/tmp/.embry0-boot-screenshot.png",
    )
    assert blob is None


# -------- vibium backend variant (#235) --------


@pytest.mark.asyncio
async def test_vibium_backend_runs_cli_with_no_sandbox_env():
    """browser_backend='vibium' drives the vibium CLI (not playwright-core)
    with the backend's --no-sandbox env; daemon stopped best-effort."""
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="")
    seen_kwargs = {}

    def fake_build(cid, cmd, **kw):
        if kw.get("user"):  # the read-out call; the screenshot run's kwargs are what we assert on
            return ["docker", "exec", "-u", kw["user"], cid, *cmd]
        seen_kwargs.update(kw)
        return ["docker", "exec", cid, *cmd]

    docker.build_exec_cmd = MagicMock(side_effect=fake_build)
    docker.run_cmd_bytes = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n...")
    docker.copy_bytes_from = AsyncMock(
        return_value=b"\x89PNG"
    )  # removed API; lets a pre-change tree reach an assertion

    blob = await take_diagnostic_screenshot(
        docker=docker,
        container_id="C",
        frontend_url="http://macrolab-frontend:3000",
        sandbox_path="/tmp/.embry0-boot-screenshot.png",
        browser_backend="vibium",
    )
    assert blob.startswith(b"\x89PNG")
    cmd = docker.run_cmd.await_args[0][0]
    script = cmd[-1]
    assert cmd[3] == "bash"
    assert "vibium --headless go" in script
    assert "vibium --headless screenshot" in script
    assert "vibium daemon stop" in script
    assert "|| true" in script  # failed navigation tolerated — error page IS the diagnostic
    assert "node" not in script and "playwright" not in script
    assert seen_kwargs == {"env": {"VIBIUM_CHROME_ARGS": "--no-sandbox"}}
    docker.run_cmd_bytes.assert_awaited_once()
    read_cmd = docker.run_cmd_bytes.await_args.args[0]
    assert read_cmd[:4] == ["docker", "exec", "-u", "agent"] and "/tmp/.embry0-boot-screenshot.png" in read_cmd


@pytest.mark.asyncio
async def test_vibium_backend_returns_none_on_run_failure():
    """Best-effort under vibium too: CLI failure -> None, never a raise."""
    docker = MagicMock()
    docker.run_cmd = AsyncMock(side_effect=RuntimeError("vibium daemon dead"))
    docker.build_exec_cmd = MagicMock(
        side_effect=lambda cid, cmd, **kw: [
            "docker",
            "exec",
            *(["-u", kw["user"]] if kw.get("user") else []),
            cid,
            *cmd,
        ]
    )
    docker.run_cmd_bytes = AsyncMock()

    blob = await take_diagnostic_screenshot(
        docker=docker,
        container_id="C",
        frontend_url="http://x:1",
        browser_backend="vibium",
    )
    assert blob is None
    docker.run_cmd_bytes.assert_not_awaited()


def test_vibium_script_shell_quotes_url_and_path():
    from embry0.workflows.qa.screenshot import _build_vibium_script

    script = _build_vibium_script("http://h:3000/a b?q='x'", "/tmp/pa th.png")
    # shlex-quoted: the single-quote-containing URL survives as one token.
    assert "'http://h:3000/a b?q='\"'\"'x'\"'\"''" in script
    assert "'/tmp/pa th.png'" in script


@pytest.mark.asyncio
async def test_playwright_backend_is_the_default_and_unchanged():
    """Regression (#235): no browser_backend argument -> the exact legacy
    playwright call — node -e script, no extra exec kwargs (the strict
    (cid, cmd) fake signature proves no kwargs are passed)."""
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="")
    docker.build_exec_cmd = MagicMock(
        side_effect=lambda cid, cmd, **kw: [
            "docker",
            "exec",
            *(["-u", kw["user"]] if kw.get("user") else []),
            cid,
            *cmd,
        ]
    )
    docker.run_cmd_bytes = AsyncMock(return_value=b"\x89PNG")
    docker.copy_bytes_from = AsyncMock(
        return_value=b"\x89PNG"
    )  # removed API; lets a pre-change tree reach an assertion

    blob = await take_diagnostic_screenshot(
        docker=docker,
        container_id="C",
        frontend_url="http://f:3000",
    )
    assert blob == b"\x89PNG"
    cmd = docker.run_cmd.await_args[0][0]
    assert cmd[3] == "node"
    assert "vibium" not in " ".join(cmd)
