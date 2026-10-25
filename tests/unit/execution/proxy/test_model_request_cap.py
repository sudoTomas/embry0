"""The model proxies forward a long agent conversation, and answer one over their cap with a clear 413.

aiohttp refuses a body over 1 MiB unless an app says otherwise. A grok
developer run 83 turns in sent a ~316k-token prompt, more than 1 MiB of JSON,
and the xai-proxy refused it with "Maximum request body size 1048576 exceeded".
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient

from embry0.execution.proxy.auth_proxy import create_auth_proxy_app
from embry0.execution.proxy.manager import ProxyManager
from embry0.execution.proxy.model_request import DEFAULT_MAX_REQUEST_BYTES
from embry0.execution.proxy.xai_proxy import create_xai_proxy_app
from embry0.safety.error_codes import ErrorCode, classify_agent_error

_ADMIN = "test-admin-secret-not-real"
_SANDBOX_TOKEN = "sandbox-bearer-token-at-least-32-chars-long"


class _FakeStream:
    def __init__(self, sent: dict, **kw: object) -> None:
        sent.update(kw)
        self.status_code = 200
        self.headers = {"content-type": "application/json"}

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *a: object) -> bool:
        return False

    async def aiter_bytes(self):  # noqa: ANN201 - an async generator
        yield b"{}"


class _FakeClient:
    def __init__(self) -> None:
        self.sent: dict = {}

    def stream(self, **kw: object) -> _FakeStream:
        return _FakeStream(self.sent, **kw)

    async def aclose(self) -> None:
        return None


def _conversation(size: int) -> bytes:
    """A messages request about ``size`` bytes long, as a long agent run sends one."""
    return json.dumps({"model": "grok-4.7", "messages": [{"role": "user", "content": "x" * size}]}).encode()


async def _ready_xai(aiohttp_client, max_request_bytes: int) -> tuple[TestClient, _FakeClient]:
    client = await aiohttp_client(create_xai_proxy_app(_ADMIN, max_request_bytes=max_request_bytes))
    admin = {"X-Admin-Token": _ADMIN}
    await client.post("/admin/enroll", json={"sandbox_id": "s1", "sandbox_token": _SANDBOX_TOKEN}, headers=admin)
    await client.post("/admin/token", json={"access_token": "at-1"}, headers=admin)
    fake = _FakeClient()
    client.app["http_client"] = fake
    return client, fake


async def _ready_auth(aiohttp_client, max_request_bytes: int) -> tuple[TestClient, _FakeClient]:
    client = await aiohttp_client(create_auth_proxy_app("sk-test", _ADMIN, max_request_bytes=max_request_bytes))
    await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": _SANDBOX_TOKEN},
        headers={"X-Admin-Token": _ADMIN},
    )
    fake = _FakeClient()
    client.app["http_client"] = fake
    return client, fake


@pytest.mark.parametrize("ready", [_ready_xai, _ready_auth], ids=["xai-proxy", "auth-proxy"])
async def test_a_request_over_a_megabyte_is_forwarded_by_default(aiohttp_client, ready) -> None:
    client, fake = await ready(aiohttp_client, DEFAULT_MAX_REQUEST_BYTES)
    body = _conversation(1_300_000)
    resp = await client.post(
        "/v1/messages",
        data=body,
        headers={"Authorization": f"Bearer {_SANDBOX_TOKEN}", "Content-Type": "application/json"},
    )
    assert resp.status == 200
    assert len(fake.sent["content"]) >= 1_300_000, "the whole conversation reached the upstream"


@pytest.mark.parametrize("ready", [_ready_xai, _ready_auth], ids=["xai-proxy", "auth-proxy"])
async def test_a_request_over_the_cap_gets_a_413_the_run_can_name(aiohttp_client, ready) -> None:
    client, fake = await ready(aiohttp_client, 4096)
    resp = await client.post(
        "/v1/messages",
        data=_conversation(8192),
        headers={"Authorization": f"Bearer {_SANDBOX_TOKEN}", "Content-Type": "application/json"},
    )
    assert resp.status == 413
    error = (await resp.json())["error"]
    assert error["type"] == "request_too_large" and "4096-byte cap" in error["message"]
    assert fake.sent == {}, "nothing over the cap reaches the upstream"
    assert classify_agent_error(error["message"]) == ErrorCode.CONTEXT_WINDOW_EXCEEDED


def test_the_old_default_refusal_reads_as_an_outgrown_conversation() -> None:
    assert classify_agent_error("Maximum request body size 1048576 exceeded.") == ErrorCode.CONTEXT_WINDOW_EXCEEDED


@pytest.mark.parametrize(
    "make",
    [
        lambda: create_xai_proxy_app(_ADMIN, max_request_bytes=0),
        lambda: create_auth_proxy_app("k", _ADMIN, max_request_bytes=-1),
    ],
)
def test_a_cap_must_be_positive(make) -> None:
    with pytest.raises(ValueError):
        make()


@pytest.mark.asyncio
async def test_the_manager_hands_the_cap_to_both_model_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("embry0.execution.proxy.manager.socket.gethostbyname", lambda host: "10.99.0.1")
    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value=b"ok")
    docker.build_run_proxy_cmd = MagicMock(
        side_effect=lambda **kw: (["docker", "run", kw.get("name", "")], "/dev/null")
    )
    docker.build_network_cmd = MagicMock(side_effect=lambda *args: ["docker", "network", *args])
    docker.build_rm_cmd = MagicMock(side_effect=lambda c: ["docker", "rm", "-f", c])
    pm = ProxyManager(docker, proxy_admin_token=_ADMIN)
    await pm.start(
        github_token="ghp_test",
        anthropic_api_key="sk-test",
        enable_auth_proxy=True,
        enable_xai_proxy=True,
        model_proxy_max_request_bytes=33_554_432,
    )
    envs = {c.kwargs["name"]: c.kwargs["env"] for c in docker.build_run_proxy_cmd.call_args_list}
    assert envs["xai-proxy"]["MAX_REQUEST_BYTES"] == "33554432"
    assert envs["auth-proxy"]["MAX_REQUEST_BYTES"] == "33554432"
    assert "MAX_REQUEST_BYTES" not in envs["git-proxy"], "only the model proxies take the model cap"
