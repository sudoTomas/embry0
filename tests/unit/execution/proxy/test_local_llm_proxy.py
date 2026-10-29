"""The local-llm-proxy forwards an enrolled sandbox's model calls to one upstream, and nothing else."""

from __future__ import annotations

from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient

from embry0.execution.proxy.local_llm_proxy import create_local_llm_proxy_app
from tests.fixtures.recording_upstream import is_test_request

_ADMIN = "test-admin-secret-not-real"
_SANDBOX_TOKEN = "sandbox-bearer-token-at-least-32-chars-long"
_SERVER_KEY = "server-key-the-sandbox-never-sees"
_API_ROOT = "/v1/"


@pytest.fixture
async def upstream(aiohttp_server) -> Any:
    """A fake model server recording the requests the proxy may send it.

    ``seen`` holds the requests under the API root the proxy forwards to, and
    any carrying the server key, wherever they go. Anything else that reaches
    its port, such as an unrelated client's ``GET /``, is answered the same way
    and kept in ``ignored`` (#221).
    """
    seen: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []

    async def record(request: web.Request) -> web.Response:
        counted = is_test_request(request.path, request.headers, paths=[_API_ROOT], credentials=[_SERVER_KEY])
        (seen if counted else ignored).append(
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
    server.ignored = ignored
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


async def _get_from_upstream(upstream: Any, path: str, **headers: str) -> int:
    """GET ``path`` from the model server's port directly, as another process on the host can."""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{upstream.port}{path}", headers=headers) as resp:
            await resp.read()
            return resp.status


async def test_a_stray_request_to_the_model_server_changes_no_refusal(aiohttp_client, upstream):
    """Any process on the host can reach the model server's port. Counted, a
    ``GET /`` from one fails the refusal tests above, which expect nothing to
    reach it, or passes a test that expects something to (#221)."""
    client = await _proxy(aiohttp_client, upstream, upstream_api_key=_SERVER_KEY)
    assert await _get_from_upstream(upstream, "/") == 200, "the stray request did not reach the model server"
    bad = {"Authorization": "Bearer someone-elses-token-that-is-long-enough"}
    refused = await client.post("/v1/chat/completions", json={}, headers=bad)
    assert await _get_from_upstream(upstream, "/favicon.ico") == 200, "the stray request did not reach the model server"

    assert refused.status == 401
    assert upstream.seen == [], (
        f"only the stray requests reached the model server, and they must not count: {upstream.seen}"
    )
    # A subset: a real stray from another process lands here too.
    strays = {(r["method"], r["path"]) for r in upstream.ignored}
    assert {("GET", "/"), ("GET", "/favicon.ico")} <= strays, f"the strays were not kept aside: {strays}"


async def test_a_request_carrying_the_server_key_counts_on_any_path(upstream):
    """A proxy that leaks the server key need not use the API root, and no other
    process has the key, so a request carrying it counts wherever it goes; one
    without it, off the API root, does not."""
    await _get_from_upstream(upstream, "/metrics", Authorization=f"Bearer {_SERVER_KEY}")
    await _get_from_upstream(upstream, "/metrics", Authorization="Bearer someone-elses-key")

    counted = [(r["path"], r["authorization"]) for r in upstream.seen]
    assert counted == [("/metrics", f"Bearer {_SERVER_KEY}")], (
        f"a request carrying the server key must count on any path, and no other: {counted}"
    )


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
