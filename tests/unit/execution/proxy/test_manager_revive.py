"""#626: a proxy found stopped is recreated, and gets back what it held.

A context-proxy exited and stayed exited: every later enroll and unenroll
against it failed with "container … is not running" until the next
orchestrator start. The proxies hold enrollments in memory only, so a
recreated proxy must also be given back the enrollments of the sandboxes
still running.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import embry0.api.app as app_module
from embry0.execution.proxy.git_scope import PUSH_BRANCHES_FIELD
from embry0.execution.proxy.manager import ProxyManager


class _Proxy:
    def __init__(self, generation: int) -> None:
        self.generation = generation
        self.running = True
        self.enrollments: dict[str, dict[str, Any]] = {}


class _Daemon:
    """The docker CLI as the proxy manager drives it: run, rm, network connect, inspect, exec."""

    def __init__(self) -> None:
        self.proxies: dict[str, _Proxy] = {}
        self.generations = 0
        self.unreachable = False

    def client(self) -> MagicMock:
        docker = MagicMock()
        docker.build_run_proxy_cmd = MagicMock(
            side_effect=lambda *, name, image, network, env: (["docker", "run", "--name", name], "/nonexistent.env")
        )
        docker.build_rm_cmd = MagicMock(side_effect=lambda name: ["docker", "rm", "-f", name])
        docker.build_network_cmd = MagicMock(side_effect=lambda op, net, name: ["docker", "network", op, net, name])
        docker.build_inspect_running_cmd = MagicMock(side_effect=lambda name: ["docker", "inspect-running", name])
        docker.build_exec_cmd = MagicMock(
            side_effect=lambda container, command, **kwargs: ["docker", "exec", container, *command]
        )
        docker.run_cmd = AsyncMock(side_effect=self._run)
        return docker

    async def _run(self, cmd: list[str], timeout: int = 60, stdin_data: bytes | None = None, **_kw: Any) -> str:
        if self.unreachable:
            raise RuntimeError(
                "Docker command failed: Cannot connect to the Docker daemon. Is the docker daemon running?"
            )
        verb = cmd[1]
        if verb == "run":
            self.generations += 1
            self.proxies[cmd[3]] = _Proxy(self.generations)
            return "cid"
        if verb == "rm":
            self.proxies.pop(cmd[3], None)
            return ""
        if verb == "network":
            return ""
        if verb == "inspect-running":
            if cmd[2] not in self.proxies:
                raise RuntimeError(f"Docker command failed: Error: No such object: {cmd[2]}")
            return "true\n" if self.proxies[cmd[2]].running else "false\n"
        if verb == "exec":
            return self._exec(cmd[2], stdin_data, cmd[-1])
        raise AssertionError(f"unexpected docker command {cmd}")

    def _exec(self, name: str, stdin_data: bytes | None, script: str) -> str:
        proxy = self.proxies.get(name)
        if proxy is None:
            raise RuntimeError(f"Docker command failed: Error response from daemon: No such container: {name}")
        if not proxy.running:
            raise RuntimeError(
                "Docker command failed: Error response from daemon: container 35166accda7e is not running"
            )
        method = re.search(r"method='(\w+)'", script).group(1)
        path = re.search(r"http://localhost:\d+(/[^']*)'", script).group(1)
        # The admin token and body arrive on stdin now (#353 review), as one JSON object.
        cfg = json.loads((stdin_data or b"{}").decode())
        body = json.loads(cfg.get("body") or "{}")
        if method == "POST" and path == "/admin/enroll":
            proxy.enrollments[body["sandbox_id"]] = body
            return json.dumps({"allowed_containers": body.get("allowed_containers")}) + "\nHTTP_CODE=200"
        if method == "DELETE" and path.startswith("/admin/enroll/"):
            found = proxy.enrollments.pop(path.removeprefix("/admin/enroll/"), None)
            return f"HTTP_CODE={200 if found else 404}"
        if method == "PUT" and path.startswith("/admin/git-push-branches/"):
            enrollment = proxy.enrollments.get(path.removeprefix("/admin/git-push-branches/"))
            if enrollment is None:
                return "HTTP_CODE=404"
            enrollment[PUSH_BRANCHES_FIELD] = body[PUSH_BRANCHES_FIELD]
            return json.dumps({PUSH_BRANCHES_FIELD: body[PUSH_BRANCHES_FIELD]}) + "\nHTTP_CODE=200"
        raise AssertionError(f"unexpected admin request {method} {path}")


async def _manager(daemon: _Daemon) -> ProxyManager:
    """A manager that launched the context-proxy, as start() does when it is enabled."""
    mgr = ProxyManager(daemon.client(), proxy_admin_token="admin-secret")
    mgr._http = MagicMock()  # start() was called
    await mgr._launch(name="context-proxy", env={"PROXY_TYPE": "context", "LISTEN_PORT": "9107"}, attach_internet=True)
    mgr.context_proxy_url = "http://context-proxy:9107"
    return mgr


async def test_an_enroll_against_a_stopped_proxy_recreates_it_and_restores_its_enrollments():
    daemon = _Daemon()
    mgr = await _manager(daemon)
    await mgr.enroll_context("sandbox-a", "tok-a")
    daemon.proxies["context-proxy"].running = False  # it exited

    try:
        await mgr.enroll_context("sandbox-b", "tok-b")
    except RuntimeError as exc:
        pytest.fail(f"an enroll against a stopped proxy failed instead of recreating it: {exc}")

    proxy = daemon.proxies["context-proxy"]
    assert proxy.running and proxy.generation == 2, "the stopped proxy was not recreated"
    assert set(proxy.enrollments) == {"sandbox-a", "sandbox-b"}, (
        f"the recreated proxy must hold the running sandboxes' enrollments again: {sorted(proxy.enrollments)}"
    )
    assert proxy.enrollments["sandbox-a"]["sandbox_token"] == "tok-a"
    assert 0 < proxy.enrollments["sandbox-a"]["ttl_seconds"] <= 86_400


async def test_an_unenroll_against_a_missing_proxy_recreates_it_and_revokes_the_sandbox():
    daemon = _Daemon()
    mgr = await _manager(daemon)
    await mgr.enroll_context("sandbox-a", "tok-a")
    await mgr.enroll_context("sandbox-b", "tok-b")
    del daemon.proxies["context-proxy"]  # removed outright

    await mgr.unenroll_sandbox("sandbox-a")

    proxy = daemon.proxies.get("context-proxy")
    assert proxy is not None and proxy.running, "the missing proxy was not recreated"
    assert set(proxy.enrollments) == {"sandbox-b"}, (
        f"sandbox-a must be revoked and sandbox-b restored: {sorted(proxy.enrollments)}"
    )


async def test_a_recreated_git_proxy_gets_the_push_branches_set_after_enrollment():
    """#559 x #626: the developer's chosen branch, set after the sandbox was
    enrolled, must survive a git-proxy recreation. Replaying the enroll body
    as it was at create would put back the create-time branches, and the
    proxy would refuse the push to the branch the job actually uses."""
    daemon = _Daemon()
    mgr = await _manager(daemon)
    await mgr._launch(name="git-proxy", env={"PROXY_TYPE": "git", "LISTEN_PORT": "9101"}, attach_internet=False)
    mgr.git_proxy_url = "http://git-proxy:9101"
    enroll = {
        "sandbox_id": "sandbox-a",
        "sandbox_token": "tok-a",
        "git_push": True,
        PUSH_BRANCHES_FIELD: ["embry0/job-a"],
    }
    await mgr._exec_admin_request(container="git-proxy", method="POST", path="/admin/enroll", port=9101, body=enroll)
    await mgr.set_git_push_branches("sandbox-a", ["fix/chosen-branch"])
    daemon.proxies["git-proxy"].running = False  # it exited

    await mgr.unenroll_sandbox("sandbox-other")  # any admin request finds it stopped and recreates it

    proxy = daemon.proxies["git-proxy"]
    assert proxy.generation > 1, "the stopped git-proxy was not recreated"
    assert proxy.enrollments["sandbox-a"][PUSH_BRANCHES_FIELD] == ["fix/chosen-branch"], (
        "the recreated git-proxy must hold the push branches set after enrollment, "
        f"not the create-time ones: {proxy.enrollments['sandbox-a'][PUSH_BRANCHES_FIELD]}"
    )
    assert enroll[PUSH_BRANCHES_FIELD] == ["embry0/job-a"], "the caller's enroll body must not be mutated"


async def test_a_revoked_enrollment_is_not_replayed():
    daemon = _Daemon()
    mgr = await _manager(daemon)
    await mgr.enroll_context("sandbox-a", "tok-a")
    await mgr.unenroll_sandbox("sandbox-a")
    daemon.proxies["context-proxy"].running = False

    await mgr.enroll_context("sandbox-b", "tok-b")

    assert set(daemon.proxies["context-proxy"].enrollments) == {"sandbox-b"}


async def test_an_expired_enrollment_is_not_replayed(monkeypatch):
    import embry0.execution.proxy.manager as manager_mod

    clock = [1000.0]
    monkeypatch.setattr(manager_mod.time, "monotonic", lambda: clock[0])
    daemon = _Daemon()
    mgr = await _manager(daemon)
    await mgr.enroll_context("sandbox-a", "tok-a")
    clock[0] += 86_401  # past its TTL: the proxy itself would have dropped it
    daemon.proxies["context-proxy"].running = False

    await mgr.enroll_context("sandbox-b", "tok-b")

    assert set(daemon.proxies["context-proxy"].enrollments) == {"sandbox-b"}


async def test_the_rules_naming_proxy_addresses_are_refreshed_after_a_recreation():
    daemon = _Daemon()
    mgr = await _manager(daemon)
    hook = AsyncMock()
    mgr.on_proxy_recreated(hook)
    await mgr.enroll_context("sandbox-a", "tok-a")
    hook.assert_not_awaited()
    daemon.proxies["context-proxy"].running = False

    await mgr.enroll_context("sandbox-b", "tok-b")

    hook.assert_awaited_once()


async def test_a_failing_refresh_does_not_fail_the_request():
    daemon = _Daemon()
    mgr = await _manager(daemon)
    mgr.on_proxy_recreated(AsyncMock(side_effect=RuntimeError("isolation chain not in place")))
    daemon.proxies["context-proxy"].running = False

    await mgr.enroll_context("sandbox-b", "tok-b")

    assert "sandbox-b" in daemon.proxies["context-proxy"].enrollments


async def test_an_unreachable_daemon_is_not_a_stopped_proxy():
    daemon = _Daemon()
    mgr = await _manager(daemon)
    daemon.unreachable = True

    with pytest.raises(RuntimeError, match="Cannot connect to the Docker daemon"):
        await mgr.enroll_context("sandbox-b", "tok-b")

    daemon.unreachable = False
    assert daemon.proxies["context-proxy"].generation == 1, "a daemon blip recreated the proxy"


async def test_a_proxy_this_process_never_launched_is_not_created():
    daemon = _Daemon()
    mgr = ProxyManager(daemon.client(), proxy_admin_token="admin-secret")
    mgr._http = MagicMock()
    mgr.context_proxy_url = "http://context-proxy:9107"

    with pytest.raises(RuntimeError, match="No such container"):
        await mgr.enroll_context("sandbox-b", "tok-b")

    assert "context-proxy" not in daemon.proxies


async def test_no_proxy_is_recreated_while_stopping():
    daemon = _Daemon()
    mgr = await _manager(daemon)
    daemon.proxies["context-proxy"].running = False
    mgr._stopping = True

    with pytest.raises(RuntimeError, match="is not running"):
        await mgr.enroll_context("sandbox-b", "tok-b")

    assert daemon.proxies["context-proxy"].generation == 1


def test_start_up_refreshes_the_proxy_rules_after_a_recreation():
    """The hook is optional on the manager; production must pass it."""
    src = Path(app_module.__file__).read_text()
    assert "proxy_mgr.on_proxy_recreated(sandbox_mgr.refresh_proxy_policy)" in src, (
        "startup no longer refreshes the proxy rules when a proxy is recreated"
    )
