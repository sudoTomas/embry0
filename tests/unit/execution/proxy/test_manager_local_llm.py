"""ProxyManager launches the local-llm-proxy for one upstream and enrolls only the sandboxes that ask."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

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


def _launch_env(docker_mock, name: str) -> dict | None:
    for call in docker_mock.build_run_proxy_cmd.call_args_list:
        if call.kwargs.get("name") == name:
            return call.kwargs["env"]
    return None


@pytest.mark.asyncio
async def test_no_upstream_launches_no_proxy(docker_mock):
    pm = ProxyManager(docker_mock, proxy_admin_token=_ADMIN_TOKEN)
    await pm.start(github_token="ghp_test")
    assert _launch_env(docker_mock, "local-llm-proxy") is None
    assert pm.local_llm_proxy_url == ""


@pytest.mark.asyncio
async def test_an_upstream_launches_the_proxy_on_the_restricted_network(docker_mock):
    pm = ProxyManager(docker_mock, proxy_admin_token=_ADMIN_TOKEN)
    await pm.start(
        github_token="ghp_test",
        local_llm_upstream="http://model-box:8000/v1",
        local_llm_api_key="server-key",
        local_llm_max_request_bytes=4096,
    )
    env = _launch_env(docker_mock, "local-llm-proxy")
    assert env == {
        "PROXY_TYPE": "local_llm",
        # DinD resolves no name the orchestrator's networks know: the address is baked in.
        "UPSTREAM_URL": "http://10.99.0.7:8000/v1",
        "UPSTREAM_API_KEY": "server-key",
        "LISTEN_PORT": "9108",
        "PROXY_ADMIN_TOKEN": _ADMIN_TOKEN,
        "MAX_REQUEST_BYTES": "4096",
    }
    call = next(c for c in docker_mock.build_run_proxy_cmd.call_args_list if c.kwargs["name"] == "local-llm-proxy")
    assert call.kwargs["network"] == "sandbox-restricted"
    assert pm.local_llm_proxy_url == "http://local-llm-proxy:9108"
    assert "local-llm-proxy" in pm.launched_proxies()

    await pm.stop()
    assert pm.local_llm_proxy_url == ""


def test_the_upstream_keeps_what_it_cannot_or_must_not_resolve():
    assert resolve_upstream_url("http://10.0.0.5:8000/v1") == "http://10.0.0.5:8000/v1"
    assert resolve_upstream_url("http://model-box/v1") == "http://10.99.0.7/v1"
    assert resolve_upstream_url("https://model-box:8443/v1") == "https://model-box:8443/v1", "the name is the cert's"
    assert resolve_upstream_url("http://nowhere.invalid:1/v1") == "http://nowhere.invalid:1/v1"


async def _enrolling_manager(monkeypatch):
    pm = ProxyManager(docker=AsyncMock(), proxy_admin_token="x" * 20)
    pm._http = object()
    pm.xai_proxy_url = "http://xai-proxy:9106"
    pm.local_llm_proxy_url = "http://local-llm-proxy:9108"
    captured: list[dict] = []

    async def fake_exec(**kwargs):
        captured.append(kwargs)
        return {}

    monkeypatch.setattr(pm, "_exec_admin_request", fake_exec)
    return pm, captured


@pytest.mark.asyncio
async def test_only_a_sandbox_that_asks_is_enrolled_with_the_local_proxy(monkeypatch):
    pm, captured = await _enrolling_manager(monkeypatch)
    await pm.enroll_sandbox("sb-plain")
    assert [c["container"] for c in captured] == ["xai-proxy"]

    captured.clear()
    token = await pm.enroll_sandbox("sb-local", local_llm=True)
    assert [c["container"] for c in captured] == ["xai-proxy", "local-llm-proxy"]
    local = next(c for c in captured if c["container"] == "local-llm-proxy")
    assert (local["port"], local["path"], local["body"]["sandbox_token"]) == (9108, "/admin/enroll", token)


@pytest.mark.asyncio
async def test_unenroll_revokes_the_local_proxy_too(monkeypatch):
    pm, captured = await _enrolling_manager(monkeypatch)
    await pm.unenroll_sandbox("sb-local")
    local = [c for c in captured if c["container"] == "local-llm-proxy"]
    assert len(local) == 1 and local[0]["method"] == "DELETE" and local[0]["accept_404"] is True
