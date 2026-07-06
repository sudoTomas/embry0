"""Tests for ProxyManager.enroll_sandbox / unenroll_sandbox.

Phase 1.5 changed the transport from direct HTTP (which the orchestrator
cannot use because the proxies live in DinD's sandbox-restricted network,
invisible from the host) to ``docker exec`` of a tiny urllib request inside
each proxy container. These tests assert behaviour against that transport.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from athanor.execution.proxy.manager import ProxyManager


def _make_docker_mock(http_code: int = 200):
    """Build a DockerClient mock whose run_cmd returns the canned HTTP_CODE marker."""
    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(
        side_effect=lambda container, command, env=None: ["docker", "exec", container, *command]
    )
    docker.run_cmd = AsyncMock(return_value=f"HTTP_CODE={http_code}")
    return docker


@pytest.fixture
def manager():
    docker = _make_docker_mock(http_code=200)
    mgr = ProxyManager(docker, proxy_admin_token="admin-secret")
    mgr.git_proxy_url = "http://git-proxy:9101"
    mgr.github_proxy_url = "http://github-proxy:9103"
    # auth proxy disabled in this fixture
    # _http is a sentinel for "ProxyManager.start() has been called"; we set
    # it to a MagicMock since enroll_sandbox checks `is None`.
    mgr._http = MagicMock()
    return mgr


@pytest.mark.asyncio
async def test_enroll_runs_docker_exec_against_each_proxy(manager):
    token = await manager.enroll_sandbox("sandbox-job-1")
    assert len(token) >= 40
    # Two execs: git-proxy + github-proxy.
    assert manager._docker.run_cmd.await_count == 2

    exec_targets = [
        call.args[0][2]  # ["docker", "exec", <container>, ...]
        for call in manager._docker.run_cmd.await_args_list
    ]
    assert "git-proxy" in exec_targets
    assert "github-proxy" in exec_targets


@pytest.mark.asyncio
async def test_enroll_passes_admin_token_via_env(manager):
    """The token must travel via -e ADMIN_TOK on the exec, not in argv."""
    await manager.enroll_sandbox("s1")

    # Inspect the env passed to build_exec_cmd for each call.
    env_calls = [call.kwargs.get("env") or {} for call in manager._docker.build_exec_cmd.call_args_list]
    assert env_calls, "build_exec_cmd was not called"
    for env in env_calls:
        assert env.get("ADMIN_TOK") == "admin-secret"
        # Body is JSON for POST enroll.
        assert env.get("ADMIN_BODY"), "ADMIN_BODY missing"
        assert "sandbox_id" in env["ADMIN_BODY"]


@pytest.mark.asyncio
async def test_enroll_raises_on_non_200():
    docker = _make_docker_mock(http_code=401)
    mgr = ProxyManager(docker, proxy_admin_token="admin")
    mgr.git_proxy_url = "http://git-proxy:9101"
    mgr._http = MagicMock()
    with pytest.raises(RuntimeError, match="HTTP 401"):
        await mgr.enroll_sandbox("s2")


@pytest.mark.asyncio
async def test_enroll_raises_when_session_not_started():
    docker = _make_docker_mock()
    mgr = ProxyManager(docker, proxy_admin_token="admin")
    mgr.git_proxy_url = "http://git-proxy:9101"
    with pytest.raises(RuntimeError, match="must be called before enroll_sandbox"):
        await mgr.enroll_sandbox("s")


@pytest.mark.asyncio
async def test_unenroll_best_effort_swallows_404():
    docker = _make_docker_mock(http_code=404)
    mgr = ProxyManager(docker, proxy_admin_token="admin")
    mgr.git_proxy_url = "http://git-proxy:9101"
    mgr._http = MagicMock()
    await mgr.unenroll_sandbox("sandbox-gone")  # no raise


@pytest.mark.asyncio
async def test_unenroll_logs_warning_on_other_failure():
    docker = _make_docker_mock(http_code=500)
    mgr = ProxyManager(docker, proxy_admin_token="admin")
    mgr.git_proxy_url = "http://git-proxy:9101"
    mgr._http = MagicMock()
    with structlog.testing.capture_logs() as captured:
        await mgr.unenroll_sandbox("sandbox-x")
    events = [e["event"] for e in captured]
    assert "sandbox_unenroll_failed" in events


def test_proxy_admin_token_required():
    docker = MagicMock()
    with pytest.raises(ValueError, match="proxy_admin_token is required"):
        ProxyManager(docker, proxy_admin_token="")


async def _manager_with_git_proxy(monkeypatch):
    pm = ProxyManager(docker=AsyncMock(), proxy_admin_token="x" * 20)
    pm._http = object()  # bypass the "start() first" guard; _exec_admin_request is patched below
    pm.git_proxy_url = "http://git-proxy:9101"
    captured = []

    async def fake_exec(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(pm, "_exec_admin_request", fake_exec)
    return pm, captured


async def test_enroll_includes_github_token_when_given(monkeypatch):
    pm, captured = await _manager_with_git_proxy(monkeypatch)
    await pm.enroll_sandbox("sandbox-1", github_token="ghp_RAVEN")
    assert captured and all(c["body"].get("github_token") == "ghp_RAVEN" for c in captured)


async def test_enroll_omits_github_token_when_none(monkeypatch):
    pm, captured = await _manager_with_git_proxy(monkeypatch)
    await pm.enroll_sandbox("sandbox-1")
    assert captured and all("github_token" not in c["body"] for c in captured)
