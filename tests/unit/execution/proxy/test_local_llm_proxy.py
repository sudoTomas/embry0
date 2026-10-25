"""The local-llm-proxy forwards an enrolled sandbox's model calls to one upstream, and nothing else."""

from __future__ import annotations

from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient

from embry0.execution.proxy.local_llm_proxy import create_local_llm_proxy_app

_ADMIN = "test-admin-secret-not-real"
_SANDBOX_TOKEN = "sandbox-bearer-token-at-least-32-chars-long"
_SERVER_KEY = "server-key-the-sandbox-never-sees"


@pytest.fixture
async def upstream(aiohttp_server) -> Any:
    """A fake model server recording every request it gets."""
    seen: list[dict[str, Any]] = []

    async def record(request: web.Request) -> web.Response:
        seen.append(
            {
                "method": request.method,
                "path": request.path,
                "authorization": request.headers.get("Authorization"),
                "body": await request.read(),
            }
        )
        if request.path.endswith("/models"):
            return web.json_response({"data": [{"id": "qwen", "max_model_len": 65536}]})
        return web.json_response({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", record)
    server = await aiohttp_server(app)
    server.seen = seen
    return server


async def _proxy(aiohttp_client, upstream: Any, **kwargs: Any) -> TestClient:
    app = create_local_llm_proxy_app(f"http://127.0.0.1:{upstream.port}/v1", _ADMIN, **kwargs)
    client = await aiohttp_client(app)
    enrolled = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "sbx-1", "sandbox_token": _SANDBOX_TOKEN},
        headers={"X-Admin-Token": _ADMIN},
    )
    assert enrolled.status == 200
    return client


_AUTH = {"Authorization": f"Bearer {_SANDBOX_TOKEN}"}


def test_the_upstream_must_be_an_http_url_and_the_admin_token_set():
    with pytest.raises(ValueError):
        create_local_llm_proxy_app("file:///etc/passwd", _ADMIN)
    with pytest.raises(ValueError):
        create_local_llm_proxy_app("http://127.0.0.1:1/v1", "")


async def test_chat_completions_and_models_reach_the_one_upstream(aiohttp_client, upstream):
    client = await _proxy(aiohttp_client, upstream, upstream_api_key=_SERVER_KEY)
    chat = await client.post("/v1/chat/completions", json={"model": "qwen", "messages": []}, headers=_AUTH)
    assert chat.status == 200
    assert (await chat.json())["choices"][0]["message"]["content"] == "ok"
    models = await client.get("/v1/models", headers=_AUTH)
    assert (await models.json())["data"][0]["max_model_len"] == 65536
    assert [(r["method"], r["path"]) for r in upstream.seen] == [
        ("POST", "/v1/chat/completions"),
        ("GET", "/v1/models"),
    ]
    # The server's key is added here; the sandbox's bearer never leaves the proxy.
    assert {r["authorization"] for r in upstream.seen} == {f"Bearer {_SERVER_KEY}"}


async def test_without_a_server_key_no_authorization_goes_upstream(aiohttp_client, upstream):
    client = await _proxy(aiohttp_client, upstream)
    await client.post("/v1/chat/completions", json={"model": "qwen", "messages": []}, headers=_AUTH)
    assert upstream.seen[0]["authorization"] is None


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/v1/completions"),
        ("POST", "/v1/embeddings"),
        ("GET", "/v1/../metrics"),
        ("POST", "/v1/models/load"),
        ("GET", "/v1/"),
        ("GET", "/metrics"),
    ],
)
async def test_every_other_path_is_refused_and_never_forwarded(aiohttp_client, upstream, method, path):
    client = await _proxy(aiohttp_client, upstream)
    resp = await client.request(method, path, headers=_AUTH)
    assert resp.status == 404
    assert upstream.seen == []


async def test_the_wrong_method_on_an_allowed_path_is_refused(aiohttp_client, upstream):
    client = await _proxy(aiohttp_client, upstream)
    assert (await client.get("/v1/chat/completions", headers=_AUTH)).status == 405
    assert (await client.post("/v1/models", headers=_AUTH)).status == 405
    assert upstream.seen == []


async def test_a_sandbox_not_enrolled_gets_nothing(aiohttp_client, upstream):
    client = await _proxy(aiohttp_client, upstream)
    assert (await client.post("/v1/chat/completions", json={})).status == 401
    bad = {"Authorization": "Bearer someone-elses-token-that-is-long-enough"}
    assert (await client.post("/v1/chat/completions", json={}, headers=bad)).status == 401
    assert upstream.seen == []


async def test_a_revoked_sandbox_gets_nothing(aiohttp_client, upstream):
    client = await _proxy(aiohttp_client, upstream)
    revoked = await client.delete("/admin/enroll/sbx-1", headers={"X-Admin-Token": _ADMIN})
    assert revoked.status == 200
    assert (await client.post("/v1/chat/completions", json={}, headers=_AUTH)).status == 401
    assert upstream.seen == []


async def test_a_request_over_the_size_limit_is_refused(aiohttp_client, upstream):
    client = await _proxy(aiohttp_client, upstream, max_request_bytes=1024)
    resp = await client.post(
        "/v1/chat/completions", data=b"x" * 2048, headers={**_AUTH, "Content-Type": "application/json"}
    )
    assert resp.status == 413
    assert "1024" in (await resp.json())["error"]["message"]
    assert upstream.seen == []
    ok = await client.post("/v1/chat/completions", data=b"{}", headers={**_AUTH, "Content-Type": "application/json"})
    assert ok.status == 200


async def test_an_unreachable_server_is_a_502(aiohttp_client):
    app = create_local_llm_proxy_app("http://127.0.0.1:9/v1", _ADMIN)
    client = await aiohttp_client(app)
    await client.post(
        "/admin/enroll",
        json={"sandbox_id": "sbx-1", "sandbox_token": _SANDBOX_TOKEN},
        headers={"X-Admin-Token": _ADMIN},
    )
    resp = await client.post("/v1/chat/completions", json={}, headers=_AUTH)
    assert resp.status == 502
