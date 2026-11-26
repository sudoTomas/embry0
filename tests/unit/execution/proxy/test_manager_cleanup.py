"""Proxy container cleanup when the DinD daemon is unreachable.

The shared proxies (git-proxy, github-proxy, …) are removed in
``ProxyManager.stop()`` and in ``start()``'s pre-clean. A connect failure is
retried; other docker errors are not. A follow-up sweep may remove only known
names this process failed to remove and that are not currently launched.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing

from embry0.execution.proxy.manager import (
    _CLEANUP_ATTEMPTS,
    _CLEANUP_BACKOFF_S,
    _PROXY_CONTAINER_NAMES,
    ProxyManager,
)

_UNREACHABLE = (
    "Docker command failed: Cannot connect to the Docker daemon at tcp://dind:2376. Is the docker daemon running?"
)


def _manager() -> tuple[ProxyManager, MagicMock]:
    docker = MagicMock()
    docker.build_rm_cmd = MagicMock(side_effect=lambda name: ["docker", "rm", "-f", name])
    docker.run_cmd = AsyncMock(return_value="")
    pm = ProxyManager(docker, proxy_admin_token="admin-secret")
    pm._http = None
    return pm, docker


@pytest.fixture
def no_sleep(monkeypatch):
    sleeps: list[float] = []

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("embry0.execution.proxy.manager.asyncio.sleep", _sleep)
    return sleeps


@pytest.mark.asyncio
async def test_stop_retries_only_while_the_daemon_is_unreachable(no_sleep):
    pm, docker = _manager()
    pm._launched = ["git-proxy", "github-proxy"]
    calls: list[str] = []

    async def run_cmd(cmd, timeout=60, log_context=None, gone_ok=False):
        calls.append(cmd[-1])
        # git-proxy recovers on the third attempt; github-proxy is a real rm error.
        if cmd[-1] == "git-proxy" and calls.count("git-proxy") < 3:
            raise RuntimeError(_UNREACHABLE)
        if cmd[-1] == "github-proxy":
            raise RuntimeError("Docker command failed: permission denied")
        return ""

    docker.run_cmd = run_cmd

    with structlog.testing.capture_logs() as logs:
        await pm.stop()

    assert calls.count("git-proxy") == 3
    assert calls.count("github-proxy") == 1
    assert no_sleep == list(_CLEANUP_BACKOFF_S[:2])
    assert pm._launched == []
    git_contexts = [c for c in calls if c == "git-proxy"]
    assert len(git_contexts) == 3

    failed = [e for e in logs if e["event"] == "proxy_container_rm_failed"]
    assert len(failed) == 1
    assert failed[0]["container"] == "github-proxy"
    assert failed[0]["phase"] == "stop"
    assert "trace_id" not in failed[0]
    assert "job_id" not in failed[0]
    # A non-connect failure is not queued for the sweep.
    assert "github-proxy" not in pm._pending_removal
    assert pm._sweep_task is None


@pytest.mark.asyncio
async def test_stop_records_a_connect_failure_with_container_and_phase(no_sleep):
    pm, docker = _manager()
    pm._launched = ["minio-proxy"]
    seen: list[dict] = []

    async def run_cmd(cmd, timeout=60, log_context=None, gone_ok=False):
        seen.append(dict(log_context or {}))
        raise RuntimeError(_UNREACHABLE)

    docker.run_cmd = run_cmd

    with structlog.testing.capture_logs() as logs:
        await pm.stop()  # must not raise — shutdown has to finish

    assert len(seen) == _CLEANUP_ATTEMPTS
    assert all(ctx["container"] == "minio-proxy" and ctx["phase"] == "stop" for ctx in seen)
    assert [ctx["attempt"] for ctx in seen] == list(range(1, _CLEANUP_ATTEMPTS + 1))
    assert no_sleep == list(_CLEANUP_BACKOFF_S)

    failed = [e for e in logs if e["event"] == "proxy_container_rm_failed"]
    assert len(failed) == 1
    assert failed[0]["container"] == "minio-proxy"
    assert failed[0]["name"] == "minio-proxy"
    assert failed[0]["phase"] == "stop"
    assert failed[0]["log_level"] == "error"
    assert "trace_id" not in failed[0]
    retrying = [e for e in logs if e["event"] == "proxy_container_rm_retrying"]
    assert len(retrying) == _CLEANUP_ATTEMPTS - 1
    assert pm._pending_removal == {"minio-proxy"}
    # Shutdown must not leave a sweep running that could rm a replacement's proxies.
    assert pm._sweep_task is None


@pytest.mark.asyncio
async def test_stop_does_not_retry_a_timeout(no_sleep):
    pm, docker = _manager()
    pm._launched = ["xai-proxy"]

    async def run_cmd(cmd, timeout=60, log_context=None, gone_ok=False):
        raise TimeoutError("docker command timed out after 8s")

    docker.run_cmd = run_cmd

    with structlog.testing.capture_logs() as logs:
        await pm.stop()

    assert docker.build_rm_cmd.call_count == 1
    assert no_sleep == []
    failed = [e for e in logs if e["event"] == "proxy_container_rm_failed"]
    assert failed[0]["container"] == "xai-proxy"
    assert failed[0]["phase"] == "stop"
    assert pm._pending_removal == set()


@pytest.mark.asyncio
async def test_start_preclean_retries_a_daemon_blip_then_launches(no_sleep, monkeypatch):
    pm, docker = _manager()
    docker.build_run_proxy_cmd = MagicMock(return_value=(["docker", "run"], "/dev/null"))
    docker.build_network_cmd = MagicMock(return_value=["docker", "network", "connect"])
    monkeypatch.setattr("embry0.execution.proxy.manager.socket.gethostbyname", lambda host: "10.0.0.8")
    attempts = {"git-proxy": 0}

    async def run_cmd(cmd, timeout=60, log_context=None, gone_ok=False):
        if cmd[1:3] == ["rm", "-f"] and cmd[-1] == "git-proxy":
            attempts["git-proxy"] += 1
            if attempts["git-proxy"] == 1:
                raise RuntimeError(_UNREACHABLE)
        return ""

    docker.run_cmd = run_cmd
    await pm.start(github_token="ghp_test")

    assert attempts["git-proxy"] == 2
    assert pm.git_proxy_url == "http://git-proxy:9101"
    assert "git-proxy" in pm._launched
    assert pm._pending_removal == set()
    assert pm._sweep_task is None
    await pm.stop()


@pytest.mark.asyncio
async def test_sweep_removes_only_known_unlaunched_leftovers(no_sleep):
    pm, docker = _manager()
    pm._launched = ["github-proxy"]
    pm._pending_removal = {"git-proxy", "github-proxy", "sandbox-job", "not-a-proxy"}
    removed: list[str] = []

    async def run_cmd(cmd, timeout=60, log_context=None, gone_ok=False):
        removed.append(cmd[-1])
        assert log_context["phase"] == "delayed_sweep"
        assert log_context["container"] == cmd[-1]
        return ""

    docker.run_cmd = run_cmd
    await pm._sweep_pending()

    assert removed == ["git-proxy"]
    assert "github-proxy" not in removed
    assert "sandbox-job" not in removed
    assert pm._pending_removal == set()
    # The live proxy is untouched.
    assert pm._launched == ["github-proxy"]


@pytest.mark.asyncio
async def test_sweep_does_not_delete_a_name_launched_after_the_failure(no_sleep):
    pm, docker = _manager()
    pm._pending_removal = {"context-proxy"}
    pm._launched = ["context-proxy"]

    await pm._sweep_pending()

    docker.run_cmd.assert_not_called()
    assert pm._pending_removal == set()
    assert pm._launched == ["context-proxy"]


@pytest.mark.asyncio
async def test_failed_preclean_of_an_unlaunched_name_is_swept_later(no_sleep, monkeypatch):
    """A connect failure on a name this process does not launch is remembered
    and removed later — not by listing every ``*-proxy`` container."""
    pm, docker = _manager()
    docker.build_run_proxy_cmd = MagicMock(return_value=(["docker", "run"], "/dev/null"))
    docker.build_network_cmd = MagicMock(return_value=["docker", "network", "connect"])
    monkeypatch.setattr("embry0.execution.proxy.manager.socket.gethostbyname", lambda host: "10.0.0.8")
    state = {"daemon_up": False}
    removed: list[str] = []

    async def run_cmd(cmd, timeout=60, log_context=None, gone_ok=False):
        if cmd[1:3] == ["rm", "-f"]:
            if not state["daemon_up"]:
                raise RuntimeError(_UNREACHABLE)
            removed.append(cmd[-1])
            return ""
        return ""

    docker.run_cmd = run_cmd
    await pm.start(github_token="", anthropic_api_key="")

    # minio/presign always launch; git-proxy does not. The failed rm of
    # git-proxy must be pending, and the launched names must not be.
    assert "git-proxy" in pm._pending_removal
    assert "minio-proxy" not in pm._pending_removal
    assert pm._sweep_task is not None
    state["daemon_up"] = True
    await pm._sweep_task
    assert "git-proxy" in removed
    assert "minio-proxy" not in removed
    assert "presign-proxy" not in removed
    for name in removed:
        assert name in _PROXY_CONTAINER_NAMES
        assert name not in pm._launched
    assert pm._pending_removal == set()
    await pm.stop()


@pytest.mark.asyncio
async def test_stop_cancels_a_remainder_still_to_send(no_sleep):
    """The proxies go: what a given-up replay did not send is moot, and its task must not outlive the manager."""
    pm, _docker = _manager()
    gate = asyncio.Event()

    async def _send_remainder(proxy: str) -> None:
        await gate.wait()

    pm._send_remainder = _send_remainder  # type: ignore[method-assign]
    pm._replay_abandoned.add("xai-proxy")
    pm._resume_replay_soon("xai-proxy")
    (task,) = pm._remainder_tasks
    try:
        await pm.stop()
        await asyncio.wait({task}, timeout=1)
        assert task.cancelled(), "a remainder still to send outlived stop"
    finally:
        gate.set()
