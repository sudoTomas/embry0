"""The proxy's path per chat-completions provider: an enrolled sandbox reaches, at ``/p/<provider>/v1``, the server
its enrollment names for that provider, with that server's key, and no provider it was not enrolled for."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient

from embry0.execution.proxy import local_llm_proxy

# The fixtures, registered here under these names. The first is autouse: the fake servers' 127.0.0.1 is let through,
# and every other refused host stays refused.
from tests.unit.execution.proxy.test_local_llm_proxy import (
    _ADMIN,
    _SANDBOX_TOKEN,
    _fake_servers_on_loopback,  # noqa: F401, E402
    create_local_llm_proxy_app,
)
from tests.unit.execution.proxy.test_local_llm_proxy import make_upstream as _make_upstream  # noqa: F401, E402


@pytest.fixture
def upstreams(_make_upstream: Any) -> Any:  # noqa: F811 — the imported fixture
    """Fake model servers, each recording what reaches it and answering with its name (``test_local_llm_proxy``)."""
    return _make_upstream


ANSWERS = Path(__file__).parents[3] / "fixtures" / "model-answers"
_H = {"X-Admin-Token": _ADMIN}
_BEARER = {"Authorization": f"Bearer {_SANDBOX_TOKEN}"}


def _server(upstream: Any, key: str, credential_id: str, name: str = "127.0.0.1") -> dict[str, str]:
    """A provider's server: the fake server's root under ``name``, and the address it was checked at."""
    return {
        "credential_id": credential_id,
        "upstream": f"http://{name}:{upstream.port}/v1/",
        "api_key": key,
        "address": "127.0.0.1",
    }


async def _enroll(client: TestClient, **fields: Any) -> Any:
    body = {"sandbox_id": "sbx-1", "sandbox_token": _SANDBOX_TOKEN, **fields}
    return await client.post("/admin/enroll", json=body, headers=_H)


async def _proxy(aiohttp_client: Any, **fields: Any) -> TestClient:
    client = await aiohttp_client(create_local_llm_proxy_app(_ADMIN))
    enrolled = await _enroll(client, **fields)
    assert enrolled.status == 200, await enrolled.text()
    return client


async def test_a_sandbox_reaches_each_provider_it_was_enrolled_for_with_that_provider_s_key(aiohttp_client, upstreams):
    openai, groq = await upstreams("openai"), await upstreams("groq")
    client = await _proxy(
        aiohttp_client,
        provider_servers={
            "openai": _server(openai, "key-openai", "cred-o"),
            "groq": _server(groq, "key-groq", "cred-g"),
        },
    )
    chat = {"model": "gpt-x", "messages": []}
    first = await client.post("/p/openai/v1/chat/completions", json=chat, headers=_BEARER)
    assert first.status == 200 and (await first.json())["choices"][0]["message"]["content"] == "openai"
    second = await client.post("/p/groq/v1/chat/completions", json=chat, headers=_BEARER)
    assert (await second.json())["choices"][0]["message"]["content"] == "groq"
    models = await client.get("/p/openai/v1/models", headers=_BEARER)
    assert models.status == 200
    assert [(r["method"], r["path"], r["authorization"]) for r in openai.seen] == [
        ("POST", "/v1/chat/completions", "Bearer key-openai"),
        ("GET", "/v1/models", "Bearer key-openai"),
    ], "a provider's server was not sent its own key, or was sent the sandbox's bearer"
    assert [r["authorization"] for r in groq.seen] == ["Bearer key-groq"]


async def test_a_provider_the_enrollment_does_not_name_is_refused(aiohttp_client, upstreams):
    openai = await upstreams("openai")
    client = await _proxy(aiohttp_client, provider_servers={"openai": _server(openai, "key-openai", "cred-o")})
    refused = await client.post("/p/deepseek/v1/chat/completions", json={"model": "m"}, headers=_BEARER)
    assert refused.status == 403, "a provider the sandbox was not enrolled for was reached"
    assert (await refused.json())["error"]["code"] == "provider_not_enrolled"
    custom = await client.post("/p/custom/v1/chat/completions", json={"model": "m"}, headers=_BEARER)
    assert custom.status == 403, "custom was reached by a sandbox enrolled with no custom server"
    legacy = await client.post("/v1/chat/completions", json={"model": "m"}, headers=_BEARER)
    assert legacy.status == 503, "the custom path's answer for a sandbox with no custom server changed"
    assert openai.seen == []


async def test_custom_is_reached_on_its_provider_path_and_on_v1(aiohttp_client, upstreams):
    local, openai = await upstreams("local"), await upstreams("openai")
    client = await _proxy(
        aiohttp_client,
        local_llm=_server(local, "key-local", "cred-c"),
        provider_servers={"openai": _server(openai, "key-openai", "cred-o")},
    )
    for path in ("/p/custom/v1/chat/completions", "/v1/chat/completions"):
        answer = await client.post(path, json={"model": "m"}, headers=_BEARER)
        assert (await answer.json())["choices"][0]["message"]["content"] == "local", path
    assert openai.seen == []


async def test_the_answer_confirms_each_provider_s_server_and_never_a_key(aiohttp_client, upstreams):
    openai = await upstreams("openai")
    client = await aiohttp_client(create_local_llm_proxy_app(_ADMIN))
    answer = await _enroll(client, provider_servers={"openai": _server(openai, "key-openai", "cred-o")})
    assert answer.status == 200
    body = await answer.json()
    assert body["provider_servers"] == {
        "openai": {"credential_id": "cred-o", "upstream": f"http://127.0.0.1:{openai.port}/v1", "address": "127.0.0.1"}
    }
    assert "local_llm" not in body
    assert "key-openai" not in await answer.text()


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"provider_servers": {}},
        {"provider_servers": []},
        {"provider_servers": {f"p{n}": {"upstream": "http://h/v1"} for n in range(17)}},
        {"provider_servers": {"OpenAI": {"upstream": "http://h/v1"}}},
        {"provider_servers": {"a/b": {"upstream": "http://h/v1"}}},
        {"provider_servers": {"custom": {"upstream": "http://h/v1"}}},
        {"provider_servers": {"openai": {"upstream": "ftp://h/v1"}}},
        {"provider_servers": {"openai": {"credential_id": "c"}}},
        {"provider_servers": {"openai": {"upstream": "http://h/v1", "api_key": "one\ntwo"}}},
        {"provider_servers": {"openai": "http://h/v1"}},
        {"provider_servers": {"openai": {"upstream": "http://169.254.169.254/v1", "address": "10.0.0.9"}}},
        {"provider_servers": {"openai": {"upstream": "http://localhost:8000/v1", "address": "10.0.0.9"}}},
        {"provider_servers": {"openai": {"upstream": "http://models.test/v1"}}},
        {"provider_servers": {"openai": {"upstream": "http://models.test/v1", "address": "models.test"}}},
        {"provider_servers": {"openai": {"upstream": "http://models.test/v1", "address": "169.254.169.254"}}},
        {"provider_servers": {"openai": {"upstream": "http://models.test/v1", "address": "0.0.0.0"}}},
    ],
    ids=[
        "neither",
        "empty",
        "not-a-map",
        "too-many",
        "upper-case",
        "slash",
        "custom",
        "scheme",
        "no-root",
        "two-line-key",
        "not-an-object",
        "link-local",
        "this-machine",
        "no-address",
        "address-a-name",
        "address-link-local",
        "address-unspecified",
    ],
)
async def test_an_enrollment_without_valid_provider_servers_is_refused(aiohttp_client, fields):
    client = await aiohttp_client(create_local_llm_proxy_app(_ADMIN))
    answer = await _enroll(client, **fields)
    assert answer.status == 400, f"taken: {fields}"
    assert (await client.post("/p/openai/v1/chat/completions", json={}, headers=_BEARER)).status == 401


async def test_an_enrollment_again_replaces_the_providers(aiohttp_client, upstreams):
    openai, groq, local = await upstreams("openai"), await upstreams("groq"), await upstreams("local")
    client = await _proxy(aiohttp_client, provider_servers={"openai": _server(openai, "k", "cred-o")})
    assert (await _enroll(client, provider_servers={"groq": _server(groq, "k", "cred-g")})).status == 200
    gone = await client.post("/p/openai/v1/chat/completions", json={}, headers=_BEARER)
    assert gone.status == 403, "a provider the newer enrollment does not name was kept"
    assert (await client.post("/p/groq/v1/chat/completions", json={}, headers=_BEARER)).status == 200
    assert (await _enroll(client, local_llm=_server(local, "k", "cred-c"))).status == 200
    assert (await client.post("/p/groq/v1/chat/completions", json={}, headers=_BEARER)).status == 403
    assert client.app[local_llm_proxy.SERVERS_BY_SANDBOX] == {}


async def test_ending_the_enrollment_forgets_the_providers(aiohttp_client, upstreams):
    openai = await upstreams("openai")
    client = await _proxy(aiohttp_client, provider_servers={"openai": _server(openai, "k", "cred-o")})
    assert (await client.delete("/admin/enroll/sbx-1", headers=_H)).status == 200
    assert client.app[local_llm_proxy.SERVERS_BY_SANDBOX] == {}, "an ended enrollment kept its provider servers"
    assert (await client.post("/p/openai/v1/chat/completions", json={}, headers=_BEARER)).status == 401


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("POST", "/p/openai/v1/embeddings", 404),
        ("POST", "/p/openai/v2/chat/completions", 404),
        ("POST", "/p/Open-AI/v1/chat/completions", 404),
        ("GET", "/p/openai/v1/chat/completions", 405),
        ("POST", "/p/openai/v1/models", 405),
    ],
)
async def test_only_the_two_paths_of_a_provider_are_served(aiohttp_client, upstreams, method, path, status):
    openai = await upstreams("openai")
    client = await _proxy(aiohttp_client, provider_servers={"openai": _server(openai, "k", "cred-o")})
    answer = await client.request(method, path, json={}, headers=_BEARER)
    assert answer.status == status
    assert openai.seen == []


async def test_a_provider_path_needs_the_sandbox_s_bearer(aiohttp_client, upstreams):
    openai = await upstreams("openai")
    client = await _proxy(aiohttp_client, provider_servers={"openai": _server(openai, "k", "cred-o")})
    for headers in ({}, {"Authorization": "Bearer someone-else-entirely-at-least-32-chars"}):
        assert (await client.post("/p/openai/v1/chat/completions", json={}, headers=headers)).status == 401
    assert openai.seen == []


async def test_a_request_over_the_size_limit_is_refused_on_a_provider_path(aiohttp_client, upstreams):
    openai = await upstreams("openai")
    client = await aiohttp_client(create_local_llm_proxy_app(_ADMIN, max_request_bytes=1024))
    assert (await _enroll(client, provider_servers={"openai": _server(openai, "k", "cred-o")})).status == 200
    answer = await client.post("/p/openai/v1/chat/completions", data=b"x" * 2048, headers=_BEARER)
    assert answer.status == 413 and openai.seen == []


async def test_a_provider_s_call_is_metered_under_the_sandbox_s_run(aiohttp_client, aiohttp_server):
    recorded = (ANSWERS / "openai-chat.json").read_bytes()

    async def answer(request: web.Request) -> web.Response:
        return web.Response(body=recorded, content_type="application/json")

    upstream_app = web.Application()
    upstream_app.router.add_post("/v1/chat/completions", answer)
    upstream = await aiohttp_server(upstream_app)
    client = await _proxy(aiohttp_client, provider_servers={"openai": _server(upstream, "k", "cred-o")})
    declared = await client.post("/admin/runs", json={"sandbox_id": "sbx-1", "run_id": "run-1"}, headers=_H)
    assert declared.status == 200, await declared.text()
    call = await client.post("/p/openai/v1/chat/completions", json={"model": "gpt-x", "messages": []}, headers=_BEARER)
    assert call.status == 200
    meter = await (await client.get("/admin/meter", headers=_H)).json()
    tally = next(t for t in meter["tallies"] if t.get("run_id") == "run-1")
    counted = next(iter(tally["models"].values()))
    assert (counted["input_tokens"] + counted.get("cache_read_tokens", 0), counted["output_tokens"]) == (1200, 80), (
        f"the provider's call was not counted under the run: {tally}"
    )


async def test_an_enrollment_naming_only_providers_drops_the_custom_server(aiohttp_client, upstreams):
    local, openai = await upstreams("local"), await upstreams("openai")
    client = await _proxy(
        aiohttp_client,
        local_llm=_server(local, "k", "cred-c"),
        provider_servers={"openai": _server(openai, "k", "cred-o")},
    )
    assert (await _enroll(client, provider_servers={"openai": _server(openai, "k", "cred-o")})).status == 200
    custom = await client.post("/p/custom/v1/chat/completions", json={}, headers=_BEARER)
    assert custom.status == 403, "a custom server the newer enrollment does not name was kept"
    assert local.seen == []


async def test_a_provider_call_connects_to_the_checked_address_and_names_the_server_in_host(aiohttp_client, upstreams):
    """As the custom server's: the proxy connects to the address the orchestrator checked, never to the name again,
    and the provider still reads its own name."""
    openai = await upstreams("openai")
    client = await _proxy(
        aiohttp_client, provider_servers={"openai": _server(openai, "k", "cred-o", name="models.test")}
    )
    answer = await client.post("/p/openai/v1/chat/completions", json={"messages": []}, headers=_BEARER)
    assert answer.status == 200, f"the call did not reach the checked address: {answer.status} {await answer.text()}"
    assert [r["host"] for r in openai.seen] == [f"models.test:{openai.port}"], openai.seen


async def test_an_https_provider_keeps_its_name_for_tls(aiohttp_client, monkeypatch):
    import httpx

    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"choices": []})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        local_llm_proxy.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(answer), **kw)
    )
    server = {
        "credential_id": "cred-o",
        "upstream": "https://api.provider.test/v1",
        "api_key": "k",
        "address": "10.0.0.8",
    }
    client = await _proxy(aiohttp_client, provider_servers={"openai": server})
    assert (await client.post("/p/openai/v1/chat/completions", json={"messages": []}, headers=_BEARER)).status == 200
    [request] = sent
    assert (request.url.scheme, request.url.host, request.url.path) == ("https", "10.0.0.8", "/v1/chat/completions")
    assert request.headers["host"] == "api.provider.test", request.headers
    assert request.extensions.get("sni_hostname") == "api.provider.test", request.extensions
    assert request.headers["authorization"] == "Bearer k"
