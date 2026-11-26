"""The model proxies forward a long agent conversation, and answer one over their cap with a clear 413.

aiohttp refuses a body over 1 MiB unless an app says otherwise. A grok
developer run 83 turns in sent a ~316k-token prompt, more than 1 MiB of JSON,
and the xai-proxy refused it with "Maximum request body size 1048576 exceeded".
"""

from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient

from embry0.execution.proxy.auth_proxy import create_auth_proxy_app
from embry0.execution.proxy.manager import ProxyManager
from embry0.execution.proxy.model_request import DEFAULT_MAX_REQUEST_BYTES
from embry0.execution.proxy.xai_proxy import create_xai_proxy_app
from embry0.safety.error_codes import ErrorCode, classify_agent_error

from .upstream_stub import route_upstream

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
    route_upstream(client.app, fake)
    return client, fake


async def _ready_auth(aiohttp_client, max_request_bytes: int) -> tuple[TestClient, _FakeClient]:
    client = await aiohttp_client(create_auth_proxy_app("sk-test", _ADMIN, max_request_bytes=max_request_bytes))
    await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": _SANDBOX_TOKEN},
        headers={"X-Admin-Token": _ADMIN},
    )
    fake = _FakeClient()
    route_upstream(client.app, fake)
    return client, fake


@pytest.mark.parametrize("ready", [_ready_xai, _ready_auth], ids=["xai-proxy", "auth-proxy"])
async def test_a_request_over_a_megabyte_is_forwarded_by_default(aiohttp_client, ready) -> None:
    client, fake = await ready(aiohttp_client, DEFAULT_MAX_REQUEST_BYTES)
    # As a stream: aiohttp warns that a body this size sent as bytes may hold up the loop (#708).
    body = io.BytesIO(_conversation(1_300_000))
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
    try:
        await pm.start(
            github_token="ghp_test",
            anthropic_api_key="sk-test",
            enable_auth_proxy=True,
            enable_xai_proxy=True,
            model_proxy_max_request_bytes=33_554_432,
        )
    finally:
        await pm.stop()  # start() opens an HTTP session that only stop() closes (#708)
    envs = {c.kwargs["name"]: c.kwargs["env"] for c in docker.build_run_proxy_cmd.call_args_list}
    assert envs["xai-proxy"]["MAX_REQUEST_BYTES"] == "33554432"
    assert envs["auth-proxy"]["MAX_REQUEST_BYTES"] == "33554432"
    assert "MAX_REQUEST_BYTES" not in envs["git-proxy"], "only the model proxies take the model cap"


class _Request:
    """A request as read_body reads it: its app's cap, the length it declares and its body."""

    def __init__(self, body: bytes, *, declared: int | None, app_cap: int = 1 << 20) -> None:
        self.app = MagicMock(_client_max_size=app_cap)
        self.content_length = declared
        self._body = body
        self.reads = 0

    async def read(self) -> bytes:
        self.reads += 1
        return self._body


@pytest.mark.parametrize("declared", [2048, None], ids=["declared", "chunked"])
async def test_a_caller_s_cap_under_the_app_s_is_the_one_enforced(declared: int | None) -> None:
    """#174: the local-llm-proxy reads with each sandbox's cap, under the app's ceiling."""
    from embry0.execution.proxy.model_request import read_body

    request = _Request(b"x" * 2048, declared=declared)
    refused = await read_body(request, "local-llm-proxy", setting="the.setting", shape="openai", limit=1024)  # type: ignore[arg-type]
    assert getattr(refused, "status", None) == 413, f"a 2048-byte body passed a 1024-byte cap: {refused!r}"
    message = json.loads(refused.body)["error"]["message"]  # type: ignore[union-attr]
    assert "1024-byte cap" in message and "the.setting" in message, message
    assert await read_body(_Request(b"x" * 1024, declared=1024), "p", limit=1024) == b"x" * 1024  # type: ignore[arg-type]


async def test_a_declared_length_over_the_cap_is_refused_before_the_body_is_read() -> None:
    """The app reads up to its ceiling; a sandbox capped lower is refused on the length it declares, before the
    proxy holds a body it would only refuse."""
    from embry0.execution.proxy.model_request import read_body

    request = _Request(b"x" * 2048, declared=2048)
    refused = await read_body(request, "p", limit=1024)  # type: ignore[arg-type]
    assert getattr(refused, "status", None) == 413
    assert request.reads == 0, "the body was read although its declared length was over the cap"
