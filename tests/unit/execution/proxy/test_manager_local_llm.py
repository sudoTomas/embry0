"""ProxyManager always launches the local-llm-proxy, and enrolls each sandbox on the custom provider with its own
credential's server (ADR 0032)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.execution.proxy import local_llm_proxy
from embry0.execution.proxy.manager import ProxyManager

_ADMIN_TOKEN = "test-admin-token"


@pytest.fixture(autouse=True)
def _resolution(monkeypatch):
    names = {"minio": "10.99.0.1", "orchestrator": "10.99.0.2", "model-box": "10.99.0.7"}

    def lookup(host: str) -> str:
        if host not in names:
            raise OSError(f"no such host {host}")
        return names[host]

    monkeypatch.setattr("embry0.execution.proxy.manager.socket.gethostbyname", lookup)


@pytest.fixture
def docker_mock():
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value=b"ok")
    docker.build_run_proxy_cmd = MagicMock(
        side_effect=lambda **kwargs: (["docker", "run", "FAKE", kwargs.get("name", "")], "/dev/null")
    )
    docker.build_network_cmd = MagicMock(side_effect=lambda *args: ["docker", "network", *args])
    docker.build_rm_cmd = MagicMock(side_effect=lambda c: ["docker", "rm", "-f", c])
    return docker


@pytest.fixture
async def pm(docker_mock) -> AsyncIterator[ProxyManager]:
    """A manager over the mocked daemon, stopped when the test ends.

    start() opens an HTTP session that only stop() closes; a test that
    started one and never stopped it left the session open (#708).
    """
    manager = ProxyManager(docker_mock, proxy_admin_token=_ADMIN_TOKEN)
    yield manager
    await manager.stop()


def _launch_env(docker_mock, name: str) -> dict | None:
    for call in docker_mock.build_run_proxy_cmd.call_args_list:
        if call.kwargs.get("name") == name:
            return call.kwargs["env"]
    return None


@pytest.mark.asyncio
async def test_the_proxy_always_launches_on_the_restricted_network_with_no_server_of_its_own(docker_mock, pm):
    await pm.start(github_token="ghp_test")
    env = _launch_env(docker_mock, "local-llm-proxy")
    # No server's address or key, and no cap: each sandbox is enrolled with its own, under the proxy's ceiling.
    assert env == {
        "PROXY_TYPE": "local_llm",
        "LISTEN_PORT": "9108",
        "PROXY_ADMIN_TOKEN": _ADMIN_TOKEN,
    }, f"the local-llm-proxy's environment: {env}"
    call = next(c for c in docker_mock.build_run_proxy_cmd.call_args_list if c.kwargs["name"] == "local-llm-proxy")
    assert call.kwargs["network"] == "sandbox-restricted"
    assert pm.local_llm_proxy_url == "http://local-llm-proxy:9108"
    assert "local-llm-proxy" in pm.launched_proxies()

    await pm.stop()
    assert pm.local_llm_proxy_url == ""


def _server() -> object:
    upstream = getattr(local_llm_proxy, "Upstream", None)
    assert upstream is not None, "a sandbox's server has no shape to be enrolled with (local_llm_proxy.Upstream)"
    return upstream(credential_id="cred-a", root="http://model-box:8000/v1/", api_key="server-key", address="10.99.0.7")


def _limits(max_request_bytes: int = 8 * 1024 * 1024, timeout_seconds: float = 900.0) -> Any:
    """The sandbox's limits at its servers (#174): the local_models section's defaults unless given."""
    limits = getattr(local_llm_proxy, "Limits", None)
    assert limits is not None, "a sandbox has no limits to be enrolled with (local_llm_proxy.Limits)"
    return limits(max_request_bytes=max_request_bytes, timeout_seconds=timeout_seconds)


async def _enrolling_manager(monkeypatch, *, echo: bool | dict = True, limits_echo: Any = True):
    pm = ProxyManager(docker=AsyncMock(), proxy_admin_token="x" * 20)
    pm._http = object()
    pm.xai_proxy_url = "http://xai-proxy:9106"
    pm.local_llm_proxy_url = "http://local-llm-proxy:9108"
    captured: list[dict] = []

    async def fake_exec(**kwargs):
        captured.append(kwargs)
        body = kwargs.get("body") or {}
        if kwargs["container"] != "local-llm-proxy" or kwargs["method"] != "POST":
            return {}
        answer: dict[str, Any] = {}
        if echo:
            echoed = {k: v for k, v in body["local_llm"].items() if k != "api_key"}
            if isinstance(echo, dict):
                echoed.update(echo)
            answer["local_llm"] = echoed
        if limits_echo is True and "limits" in body:
            answer["limits"] = dict(body["limits"])
        elif limits_echo is not False:
            answer["limits"] = limits_echo
        return answer

    monkeypatch.setattr(pm, "_exec_admin_request", fake_exec)
    return pm, captured


@pytest.mark.asyncio
async def test_only_a_sandbox_on_the_custom_provider_is_enrolled_with_the_local_proxy(monkeypatch):
    pm, captured = await _enrolling_manager(monkeypatch)
    await pm.enroll_sandbox("sb-plain")
    assert [c["container"] for c in captured] == ["xai-proxy"]
    assert all("local_llm" not in c["body"] for c in captured), "a sandbox off the custom provider was sent a server"

    captured.clear()
    token = await pm.enroll_sandbox("sb-local", local_llm=_server(), limits=_limits())
    assert [c["container"] for c in captured] == ["xai-proxy", "local-llm-proxy"]
    local = next(c for c in captured if c["container"] == "local-llm-proxy")
    assert (local["port"], local["path"], local["body"]["sandbox_token"]) == (9108, "/admin/enroll", token)
    # The name stays in the root, for Host and TLS; the proxy connects to the checked address (DinD resolves none).
    assert local["body"]["local_llm"] == {
        "credential_id": "cred-a",
        "upstream": "http://model-box:8000/v1",
        "address": "10.99.0.7",
        "api_key": "server-key",
    }, f"the server the sandbox was enrolled with: {local['body']['local_llm']}"
    xai = next(c for c in captured if c["container"] == "xai-proxy")
    assert "local_llm" not in xai["body"], "another proxy was sent the model server's key"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "echo",
    [False, {"upstream": "http://10.99.0.8:8000/v1"}, {"address": "10.99.0.8"}],
    ids=["no-echo", "another-server", "another-address"],
)
async def test_a_local_proxy_that_does_not_confirm_the_server_is_refused(monkeypatch, echo):
    """An image from before per-sandbox servers would take the enrollment and forward nowhere it was told, and one
    that holds another server would send the sandbox there: the sandbox is unenrolled there and its creation fails."""
    pm, captured = await _enrolling_manager(monkeypatch, echo=echo)
    with pytest.raises(RuntimeError, match="did not confirm"):
        await pm.enroll_sandbox("sb-local", local_llm=_server(), limits=_limits())
    revoked = [c for c in captured if c["container"] == "local-llm-proxy" and c["method"] == "DELETE"]
    assert len(revoked) == 1 and revoked[0]["path"] == "/admin/enroll/sb-local", f"not revoked: {captured}"


@pytest.mark.asyncio
async def test_a_sandbox_on_the_custom_provider_without_the_proxy_is_refused(monkeypatch):
    pm, captured = await _enrolling_manager(monkeypatch)
    pm.local_llm_proxy_url = ""
    with pytest.raises(RuntimeError, match="local-llm-proxy is not running"):
        await pm.enroll_sandbox("sb-local", local_llm=_server(), limits=_limits())
    assert captured == [], "a sandbox with no route to its server was enrolled anywhere"


@pytest.mark.asyncio
async def test_unenroll_revokes_the_local_proxy_too(monkeypatch):
    pm, captured = await _enrolling_manager(monkeypatch)
    await pm.unenroll_sandbox("sb-local")
    local = [c for c in captured if c["container"] == "local-llm-proxy"]
    assert len(local) == 1 and local[0]["method"] == "DELETE" and local[0]["accept_404"] is True


@pytest.mark.asyncio
async def test_the_sandbox_s_limits_are_sent_with_its_server(monkeypatch):
    """#174: the request cap and the timeout the sandbox manager read from local_models reach the proxy, once for
    every server the sandbox is enrolled for."""
    pm, captured = await _enrolling_manager(monkeypatch)
    await pm.enroll_sandbox("sb-local", local_llm=_server(), limits=_limits(2048, 60.0))
    local = next(c for c in captured if c["container"] == "local-llm-proxy")
    assert local["body"].get("limits") == {"max_request_bytes": 2048, "timeout_seconds": 60.0}, local["body"]
    assert all("limits" not in c["body"] for c in captured if c["container"] != "local-llm-proxy")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limits_echo",
    [False, None, {"max_request_bytes": 4096, "timeout_seconds": 60.0}, {"max_request_bytes": 2048}],
    ids=["no-echo", "null", "another-cap", "no-timeout"],
)
async def test_a_local_proxy_that_does_not_confirm_the_limits_is_refused(monkeypatch, limits_echo):
    """An image from before #174 echoes no limits and holds none: the sandbox would run under no cap of its own."""
    pm, captured = await _enrolling_manager(monkeypatch, limits_echo=limits_echo)
    with pytest.raises(RuntimeError, match="did not confirm the sandbox's limits"):
        await pm.enroll_sandbox("sb-local", local_llm=_server(), limits=_limits(2048, 60.0))
    revoked = [c for c in captured if c["container"] == "local-llm-proxy" and c["method"] == "DELETE"]
    assert len(revoked) == 1 and revoked[0]["path"] == "/admin/enroll/sb-local", f"not revoked: {captured}"


@pytest.mark.asyncio
async def test_a_sandbox_enrolled_for_its_server_without_limits_is_refused(monkeypatch):
    pm, captured = await _enrolling_manager(monkeypatch)
    with pytest.raises(ValueError, match="limits"):
        await pm.enroll_sandbox("sb-local", local_llm=_server())
    assert captured == [], "a sandbox with no limits was enrolled anywhere"
