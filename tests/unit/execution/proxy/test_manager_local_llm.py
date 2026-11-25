"""ProxyManager always launches the local-llm-proxy, and enrolls each sandbox on the custom provider with its own
credential's server (ADR 0032)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.execution.proxy import local_llm_proxy
from embry0.execution.proxy.manager import ProxyManager, resolve_upstream_url

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
    await pm.start(github_token="ghp_test", local_llm_max_request_bytes=4096)
    env = _launch_env(docker_mock, "local-llm-proxy")
    # No server's address or key: each sandbox is enrolled with its own.
    assert env == {
        "PROXY_TYPE": "local_llm",
        "LISTEN_PORT": "9108",
        "PROXY_ADMIN_TOKEN": _ADMIN_TOKEN,
        "MAX_REQUEST_BYTES": "4096",
    }, f"the local-llm-proxy's environment: {env}"
    call = next(c for c in docker_mock.build_run_proxy_cmd.call_args_list if c.kwargs["name"] == "local-llm-proxy")
    assert call.kwargs["network"] == "sandbox-restricted"
    assert pm.local_llm_proxy_url == "http://local-llm-proxy:9108"
    assert "local-llm-proxy" in pm.launched_proxies()

    await pm.stop()
    assert pm.local_llm_proxy_url == ""


@pytest.mark.asyncio
async def test_no_cap_keeps_the_proxy_s_default(docker_mock, pm):
    await pm.start(github_token="ghp_test")
    assert "MAX_REQUEST_BYTES" not in (_launch_env(docker_mock, "local-llm-proxy") or {}), (
        "a cap of 0 must leave the proxy its own default"
    )


def test_the_upstream_keeps_what_it_cannot_or_must_not_resolve():
    assert resolve_upstream_url("http://10.0.0.5:8000/v1") == "http://10.0.0.5:8000/v1"
    assert resolve_upstream_url("http://model-box/v1") == "http://10.99.0.7/v1"
    assert resolve_upstream_url("https://model-box:8443/v1") == "https://model-box:8443/v1", "the name is the cert's"
    assert resolve_upstream_url("http://nowhere.invalid:1/v1") == "http://nowhere.invalid:1/v1"


def _server() -> object:
    upstream = getattr(local_llm_proxy, "Upstream", None)
    assert upstream is not None, "a sandbox's server has no shape to be enrolled with (local_llm_proxy.Upstream)"
    return upstream(credential_id="cred-a", root="http://model-box:8000/v1/", api_key="server-key")


async def _enrolling_manager(monkeypatch, *, echo: bool | str = True):
    pm = ProxyManager(docker=AsyncMock(), proxy_admin_token="x" * 20)
    pm._http = object()
    pm.xai_proxy_url = "http://xai-proxy:9106"
    pm.local_llm_proxy_url = "http://local-llm-proxy:9108"
    captured: list[dict] = []

    async def fake_exec(**kwargs):
        captured.append(kwargs)
        body = kwargs.get("body") or {}
        if echo and kwargs["container"] == "local-llm-proxy" and kwargs["method"] == "POST":
            server = body["local_llm"]
            upstream = echo if isinstance(echo, str) else server["upstream"]
            return {"local_llm": {"credential_id": server["credential_id"], "upstream": upstream}}
        return {}

    monkeypatch.setattr(pm, "_exec_admin_request", fake_exec)
    return pm, captured


@pytest.mark.asyncio
async def test_only_a_sandbox_on_the_custom_provider_is_enrolled_with_the_local_proxy(monkeypatch):
    pm, captured = await _enrolling_manager(monkeypatch)
    await pm.enroll_sandbox("sb-plain")
    assert [c["container"] for c in captured] == ["xai-proxy"]
    assert all("local_llm" not in c["body"] for c in captured), "a sandbox off the custom provider was sent a server"

    captured.clear()
    token = await pm.enroll_sandbox("sb-local", local_llm=_server())
    assert [c["container"] for c in captured] == ["xai-proxy", "local-llm-proxy"]
    local = next(c for c in captured if c["container"] == "local-llm-proxy")
    assert (local["port"], local["path"], local["body"]["sandbox_token"]) == (9108, "/admin/enroll", token)
    # DinD resolves no name the orchestrator's networks know: the address goes in its place.
    assert local["body"]["local_llm"] == {
        "credential_id": "cred-a",
        "upstream": "http://10.99.0.7:8000/v1",
        "api_key": "server-key",
    }, f"the server the sandbox was enrolled with: {local['body']['local_llm']}"
    xai = next(c for c in captured if c["container"] == "xai-proxy")
    assert "local_llm" not in xai["body"], "another proxy was sent the model server's key"


@pytest.mark.asyncio
@pytest.mark.parametrize("echo", [False, "http://10.99.0.8:8000/v1"], ids=["no-echo", "another-server"])
async def test_a_local_proxy_that_does_not_confirm_the_server_is_refused(monkeypatch, echo):
    """An image from before per-sandbox servers would take the enrollment and forward nowhere it was told, and one
    that holds another server would send the sandbox there: the sandbox is unenrolled there and its creation fails."""
    pm, captured = await _enrolling_manager(monkeypatch, echo=echo)
    with pytest.raises(RuntimeError, match="did not confirm"):
        await pm.enroll_sandbox("sb-local", local_llm=_server())
    revoked = [c for c in captured if c["container"] == "local-llm-proxy" and c["method"] == "DELETE"]
    assert len(revoked) == 1 and revoked[0]["path"] == "/admin/enroll/sb-local", f"not revoked: {captured}"


@pytest.mark.asyncio
async def test_a_sandbox_on_the_custom_provider_without_the_proxy_is_refused(monkeypatch):
    pm, captured = await _enrolling_manager(monkeypatch)
    pm.local_llm_proxy_url = ""
    with pytest.raises(RuntimeError, match="local-llm-proxy is not running"):
        await pm.enroll_sandbox("sb-local", local_llm=_server())
    assert captured == [], "a sandbox with no route to its server was enrolled anywhere"


@pytest.mark.asyncio
async def test_unenroll_revokes_the_local_proxy_too(monkeypatch):
    pm, captured = await _enrolling_manager(monkeypatch)
    await pm.unenroll_sandbox("sb-local")
    local = [c for c in captured if c["container"] == "local-llm-proxy"]
    assert len(local) == 1 and local[0]["method"] == "DELETE" and local[0]["accept_404"] is True
