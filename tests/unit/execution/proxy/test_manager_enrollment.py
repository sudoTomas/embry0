"""Tests for ProxyManager.enroll_sandbox / unenroll_sandbox.

Phase 1.5 changed the transport from direct HTTP (which the orchestrator
cannot use because the proxies live in DinD's sandbox-restricted network,
invisible from the host) to ``docker exec`` of a tiny urllib request inside
each proxy container. These tests assert behaviour against that transport.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from embry0.execution.proxy.manager import ProxyManager


def _make_docker_mock(http_code: int = 200):
    """Build a DockerClient mock whose run_cmd returns the canned HTTP_CODE marker.

    The body echoes an empty repo scope, as a current forge API proxy does for
    a sandbox enrolled with no repository (#520), and the git-proxy's empty,
    read-only grant (#521); other proxies ignore it.
    """
    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(
        side_effect=lambda container, command, **kwargs: ["docker", "exec", container, *command]
    )
    docker.run_cmd = AsyncMock(return_value=f'{{"allowed_repos": [], "git_push": false}}\nHTTP_CODE={http_code}')
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
async def test_enroll_keeps_the_admin_token_and_the_bearer_off_argv(manager):
    """The admin token and the body (bearer + forge token) go in on stdin, never on the exec argv."""
    token = await manager.enroll_sandbox("s1")

    # build_exec_cmd is called with no env, and interactive stdin.
    for call in manager._docker.build_exec_cmd.call_args_list:
        assert call.kwargs.get("env") in (None, {}), "a secret must not ride -e on the exec"
        assert call.kwargs.get("interactive") is True, "the admin request reads its secrets from stdin"

    # Every rendered argv, and run_cmd's own argv, is free of both secrets.
    for call in manager._docker.run_cmd.await_args_list:
        argv = call.args[0]
        joined = " ".join(map(str, argv))
        assert "admin-secret" not in joined, "the admin token reached a docker exec command line"
        assert token not in joined, "the sandbox bearer reached a docker exec command line"

    # The admin token and the bearer are in the stdin blob instead.
    stdins = [call.kwargs.get("stdin_data") for call in manager._docker.run_cmd.await_args_list]
    assert stdins and all(s is not None for s in stdins), "no stdin was sent"
    first = stdins[0].decode()
    assert "admin-secret" in first and "sandbox_id" in first


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
async def test_unenroll_retries_then_logs_error_on_persistent_failure(monkeypatch):
    """RAV-1070: unenroll is authoritative — 3 attempts per proxy, ERROR on final failure."""
    import asyncio as _asyncio

    monkeypatch.setattr(_asyncio, "sleep", AsyncMock())
    docker = _make_docker_mock(http_code=500)
    mgr = ProxyManager(docker, proxy_admin_token="admin")
    mgr.git_proxy_url = "http://git-proxy:9101"
    mgr._http = MagicMock()
    with structlog.testing.capture_logs() as captured:
        await mgr.unenroll_sandbox("sandbox-x")
    assert docker.run_cmd.await_count == 3
    events = [e["event"] for e in captured]
    assert events.count("sandbox_unenroll_retrying") == 2
    assert "sandbox_unenroll_failed" in events
    final = next(e for e in captured if e["event"] == "sandbox_unenroll_failed")
    assert final["log_level"] == "error"


@pytest.mark.asyncio
async def test_unenroll_transient_failure_recovers_on_retry(monkeypatch):
    """First attempt 500s, second succeeds — no error logged, loop moves on."""
    import asyncio as _asyncio

    monkeypatch.setattr(_asyncio, "sleep", AsyncMock())
    docker = _make_docker_mock()
    docker.run_cmd = AsyncMock(side_effect=["HTTP_CODE=500", "HTTP_CODE=200"])
    mgr = ProxyManager(docker, proxy_admin_token="admin")
    mgr.git_proxy_url = "http://git-proxy:9101"
    mgr._http = MagicMock()
    with structlog.testing.capture_logs() as captured:
        await mgr.unenroll_sandbox("sandbox-x")
    events = [e["event"] for e in captured]
    assert "sandbox_unenroll_failed" not in events
    assert docker.run_cmd.await_count == 2


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
        # A current git-proxy echoes the grant it stored (#521).
        body = kwargs.get("body") or {}
        return {k: body[k] for k in ("allowed_repos", "git_upstream", "git_push", "git_push_branches") if k in body}

    monkeypatch.setattr(pm, "_exec_admin_request", fake_exec)
    return pm, captured


async def test_enroll_includes_github_token_when_given(monkeypatch):
    pm, captured = await _manager_with_git_proxy(monkeypatch)
    await pm.enroll_sandbox("sandbox-1", github_token="ghp_ACME")
    assert captured and all(c["body"].get("github_token") == "ghp_ACME" for c in captured)


async def test_enroll_omits_github_token_when_none(monkeypatch):
    pm, captured = await _manager_with_git_proxy(monkeypatch)
    await pm.enroll_sandbox("sandbox-1")
    assert captured and all("github_token" not in c["body"] for c in captured)


# --- context-proxy (RAV-1140): per-job opt-in, NOT in the broadcast ---


async def test_enroll_broadcast_excludes_context_proxy(monkeypatch):
    """Log access is per-job opt-in: the running context-proxy must NOT be
    enrolled by the generic enroll_sandbox broadcast."""
    pm, captured = await _manager_with_git_proxy(monkeypatch)
    pm.context_proxy_url = "http://context-proxy:9107"
    await pm.enroll_sandbox("sandbox-1")
    assert captured
    assert all(c["container"] != "context-proxy" for c in captured)


async def test_enroll_context_posts_to_context_proxy(monkeypatch):
    pm, captured = await _manager_with_git_proxy(monkeypatch)
    pm.context_proxy_url = "http://context-proxy:9107"
    await pm.enroll_context("sandbox-1", "tok-" + "x" * 40)
    assert len(captured) == 1
    call = captured[0]
    assert call["container"] == "context-proxy"
    assert call["port"] == 9107
    assert call["path"] == "/admin/enroll"
    assert call["body"] == {
        "sandbox_id": "sandbox-1",
        "sandbox_token": "tok-" + "x" * 40,
        "ttl_seconds": 86_400,
    }


async def test_enroll_context_sends_the_container_scope(monkeypatch):
    """SH-P4.4: a developer job is enrolled for the case's services only."""
    pm, captured = await _manager_with_git_proxy(monkeypatch)
    pm.context_proxy_url = "http://context-proxy:9107"

    async def fake_exec(**kwargs):
        captured.append(kwargs)
        return {"status": "enrolled", "allowed_containers": ["acme-backend"]}

    monkeypatch.setattr(pm, "_exec_admin_request", fake_exec)
    granted = await pm.enroll_context("sandbox-1", "tok-" + "x" * 40, containers=["acme-backend"])
    assert granted == ["acme-backend"]
    assert captured[0]["body"]["allowed_containers"] == ["acme-backend"]


async def test_enroll_context_refuses_a_proxy_that_ignored_the_scope(monkeypatch):
    """A proxy image built before SH-P4.4 accepts the body, ignores the scope and
    grants the FULL allowlist. The sandbox must be unenrolled and the grant refused."""
    pm, captured = await _manager_with_git_proxy(monkeypatch)
    pm.context_proxy_url = "http://context-proxy:9107"

    async def fake_exec(**kwargs):
        captured.append(kwargs)
        return {"status": "enrolled"}

    monkeypatch.setattr(pm, "_exec_admin_request", fake_exec)
    with pytest.raises(RuntimeError, match="did not confirm the container scope"):
        await pm.enroll_context("sandbox-1", "tok-" + "x" * 40, containers=["acme-backend"])
    assert [c["method"] for c in captured] == ["POST", "DELETE"]
    assert captured[1]["path"] == "/admin/enroll/sandbox-1"


async def test_enroll_context_refuses_an_empty_scope(monkeypatch):
    pm, captured = await _manager_with_git_proxy(monkeypatch)
    pm.context_proxy_url = "http://context-proxy:9107"
    with pytest.raises(ValueError, match="empty"):
        await pm.enroll_context("sandbox-1", "tok-" + "x" * 40, containers=[])
    assert captured == []


async def test_enroll_context_raises_when_proxy_not_running(monkeypatch):
    pm, _ = await _manager_with_git_proxy(monkeypatch)
    with pytest.raises(RuntimeError, match="context-proxy is not running"):
        await pm.enroll_context("sandbox-1", "tok-" + "x" * 40)


async def test_unenroll_includes_context_proxy_when_running(monkeypatch):
    pm, captured = await _manager_with_git_proxy(monkeypatch)
    pm.context_proxy_url = "http://context-proxy:9107"
    await pm.unenroll_sandbox("sandbox-1")
    assert any(c["container"] == "context-proxy" and c["method"] == "DELETE" for c in captured)


# --- the admin transport returns the response body (SH-P4.4) ---


async def test_admin_request_script_returns_the_json_body(aiohttp_server):
    """Runs the REAL generated script against a local server: the body must
    survive the docker-exec stdout transport alongside the HTTP_CODE marker."""
    import asyncio
    import sys

    from aiohttp import web

    async def enroll(request):
        assert request.headers["X-Admin-Token"] == "x" * 20
        body = await request.json()
        return web.json_response({"status": "enrolled", "allowed_containers": body["allowed_containers"]})

    app = web.Application()
    app.router.add_post("/admin/enroll", enroll)
    server = await aiohttp_server(app)

    def build_exec_cmd(container, argv, **kwargs):
        assert kwargs.get("interactive") is True, "the admin request must read its secrets from stdin"
        return [sys.executable, *argv[1:]]

    async def run_cmd(cmd, timeout=None, stdin_data=None):
        assert stdin_data is not None, "the admin token and body must arrive on stdin"
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate(stdin_data)
        assert proc.returncode == 0, err.decode()
        return out.decode()

    docker = AsyncMock()
    docker.build_exec_cmd = build_exec_cmd
    docker.run_cmd = run_cmd
    pm = ProxyManager(docker=docker, proxy_admin_token="x" * 20)
    result = await pm._exec_admin_request(
        container="context-proxy",
        method="POST",
        path="/admin/enroll",
        port=server.port,
        body={"allowed_containers": ["acme-backend"]},
    )
    assert result == {"status": "enrolled", "allowed_containers": ["acme-backend"]}
