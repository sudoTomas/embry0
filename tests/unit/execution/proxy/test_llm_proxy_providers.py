"""The proxy's path per chat-completions provider: an enrolled sandbox reaches, at ``/p/<provider>/v1``, the server
its enrollment names for that provider, with that server's key, and no provider it was not enrolled for."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient

from embry0.execution.proxy import llm_proxy

# The fixtures, registered here under these names. The first is autouse: the fake servers' 127.0.0.1 is let through,
# and every other refused host stays refused.
from tests.unit.execution.proxy.test_llm_proxy import (
    _ADMIN,
    _SANDBOX_TOKEN,
    LIMITS,
    _fake_servers_on_loopback,  # noqa: F401, E402
    create_llm_proxy_app,
)
from tests.unit.execution.proxy.test_llm_proxy import make_upstream as _make_upstream  # noqa: F401, E402


@pytest.fixture
def upstreams(_make_upstream: Any) -> Any:  # noqa: F811 — the imported fixture
    """Fake model servers, each recording what reaches it and answering with its name (``test_llm_proxy``)."""
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
    """Enroll sbx-1 with ``fields``, and the section's default limits unless ``fields`` names its own (#174)."""
    body = {"sandbox_id": "sbx-1", "sandbox_token": _SANDBOX_TOKEN, "limits": LIMITS, **fields}
    return await client.post("/admin/enroll", json=body, headers=_H)


async def _proxy(aiohttp_client: Any, **fields: Any) -> TestClient:
    client = await aiohttp_client(create_llm_proxy_app(_ADMIN))
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
    client = await aiohttp_client(create_llm_proxy_app(_ADMIN))
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
    client = await aiohttp_client(create_llm_proxy_app(_ADMIN))
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
    assert client.app[llm_proxy.SERVERS_BY_SANDBOX] == {}


async def test_ending_the_enrollment_forgets_the_providers(aiohttp_client, upstreams):
    openai = await upstreams("openai")
    client = await _proxy(aiohttp_client, provider_servers={"openai": _server(openai, "k", "cred-o")})
    assert (await client.delete("/admin/enroll/sbx-1", headers=_H)).status == 200
    assert client.app[llm_proxy.SERVERS_BY_SANDBOX] == {}, "an ended enrollment kept its provider servers"
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
    client = await aiohttp_client(create_llm_proxy_app(_ADMIN, max_request_bytes=1024))
    servers = {"openai": _server(openai, "k", "cred-o")}
    # The sandbox's cap, at the ceiling this proxy started with (#174).
    assert (await _enroll(client, provider_servers=servers, limits={**LIMITS, "max_request_bytes": 1024})).status == 200
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


async def test_an_https_provider_keeps_its_name_for_tls(aiohttp_client, aiohttp_server, tmp_path, monkeypatch):
    """The call goes to the provider's URL, its name the Host and the TLS server name, over a connection to the
    checked address, with the provider's key."""
    from tests.fixtures.tls_servers import tls_server

    tls = await tls_server(aiohttp_server, tmp_path, monkeypatch, "api.provider.test")
    server = {
        "credential_id": "cred-o",
        "upstream": f"https://api.provider.test:{tls.port}/v1",
        "api_key": "k",
        "address": "127.0.0.1",
    }
    client = await _proxy(aiohttp_client, provider_servers={"openai": server})
    answer = await client.post("/p/openai/v1/chat/completions", json={"messages": []}, headers=_BEARER)
    assert answer.status == 200, await answer.text()
    assert tls.seen == [{"host": f"api.provider.test:{tls.port}", "authorization": "Bearer k"}], tls.seen
    assert tls.handshakes == ["api.provider.test"], f"the TLS server name must be the provider's: {tls.handshakes}"


# --- #174: one set of limits per sandbox, on every path it calls ---------------------------------------------------


async def test_each_path_of_a_sandbox_is_held_to_its_one_request_cap(aiohttp_client, upstreams):
    """The custom server's path and every provider's take the sandbox's limits from its enrollment: a body over its
    cap is refused on each, naming the setting, and reaches no server."""
    custom, openai = await upstreams("custom"), await upstreams("openai")
    client = await _proxy(
        aiohttp_client,
        local_llm=_server(custom, "key-custom", "cred-c"),
        provider_servers={"openai": _server(openai, "key-openai", "cred-o")},
        limits={**LIMITS, "max_request_bytes": 1024},
    )
    big, small = b"x" * 2048, b"{}"
    headers = {**_BEARER, "Content-Type": "application/json"}
    statuses = {}
    for path in ("/v1/chat/completions", "/p/custom/v1/chat/completions", "/p/openai/v1/chat/completions"):
        refused = await client.post(path, data=big, headers=headers)
        answered = await client.post(path, data=small, headers=headers)
        statuses[path] = (refused.status, answered.status)
        if refused.status == 413:
            assert "local_models.max_request_bytes" in (await refused.json())["error"]["message"]
    assert statuses == {path: (413, 200) for path in statuses}, f"a path was not held to the sandbox's cap: {statuses}"
    assert all(len(r.get("body", b"")) <= 1024 for r in custom.seen + openai.seen), "a body over the cap was sent"


async def test_a_provider_s_server_has_the_sandbox_s_timeout_to_answer(aiohttp_client, aiohttp_server):
    import asyncio

    async def slow(_request: web.Request) -> web.Response:
        await asyncio.sleep(0.6)
        return web.json_response({"choices": []})

    app = web.Application()
    app.router.add_post("/v1/chat/completions", slow)
    server = await aiohttp_server(app)
    client = await _proxy(
        aiohttp_client,
        provider_servers={"openai": _server(server, "key-openai", "cred-o")},
        limits={**LIMITS, "timeout_seconds": 0.2},
    )
    resp = await client.post("/p/openai/v1/chat/completions", json={"messages": []}, headers=_BEARER)
    assert resp.status == 504, f"a provider's server slower than the sandbox's timeout was waited for: {resp.status}"


@pytest.mark.parametrize("limits", [None, "8MiB", {"max_request_bytes": 1024}], ids=["none", "not-an-object", "half"])
async def test_an_enrollment_for_providers_alone_still_names_its_limits(aiohttp_client, upstreams, limits):
    openai = await upstreams("openai")
    client = await aiohttp_client(create_llm_proxy_app(_ADMIN))
    fields: dict[str, Any] = {"provider_servers": {"openai": _server(openai, "key-openai", "cred-o")}}
    body = {"sandbox_id": "sbx-1", "sandbox_token": _SANDBOX_TOKEN, **fields}
    if limits is not None:
        body["limits"] = limits
    resp = await client.post("/admin/enroll", json=body, headers=_H)
    assert resp.status == 400 and "limits" in await resp.text(), f"enrolled with limits {limits!r}: {await resp.text()}"
    assert "sbx-1" not in client.server.app[llm_proxy.SERVERS_BY_SANDBOX]


# --- a run held to its provider (ADR 0033) ---------------------------------------------------------------------------


async def _declare(client: TestClient, run_id: str = "run-1", **fields: Any) -> Any:
    return await client.post("/admin/runs", json={"sandbox_id": "sbx-1", "run_id": run_id, **fields}, headers=_H)


async def test_while_a_run_is_current_only_its_provider_is_reached(aiohttp_client, upstreams):
    openai, groq, local = await upstreams("openai"), await upstreams("groq"), await upstreams("local")
    client = await _proxy(
        aiohttp_client,
        provider_servers={
            "openai": _server(openai, "key-openai", "cred-o"),
            "groq": _server(groq, "key-groq", "cred-g"),
        },
        local_llm=_server(local, "key-local", "cred-l"),
    )
    declared = await _declare(client, provider="openai")
    assert declared.status == 200 and (await declared.json()).get("provider") == "openai", await declared.text()
    chat = {"model": "gpt-x", "messages": []}

    assert (await client.post("/p/openai/v1/chat/completions", json=chat, headers=_BEARER)).status == 200
    for path in ("/p/groq/v1/chat/completions", "/v1/chat/completions", "/p/custom/v1/chat/completions"):
        refused = await client.post(path, json=chat, headers=_BEARER)
        assert refused.status == 403, f"{path} was answered {refused.status} during a run on openai"
        assert (await refused.json())["error"]["code"] == "provider_not_declared"
    assert (await client.get("/p/groq/v1/models", headers=_BEARER)).status == 403
    assert groq.seen == [] and local.seen == [], "a call to another provider reached its server during the run"

    meter = await (await client.get("/admin/meter", headers=_H)).json()
    tally = next(t for t in meter["tallies"] if t.get("run_id") == "run-1")
    assert tally["provider"] == "openai", f"the tally does not say whose calls it holds: {tally}"

    ended = await client.delete("/admin/runs/run-1", json={}, headers=_H)
    assert ended.status == 200
    after = await client.post("/p/groq/v1/chat/completions", json=chat, headers=_BEARER)
    assert after.status == 200, "once the run ended, the sandbox's other provider is refused still"


async def test_a_run_declared_on_no_provider_holds_the_sandbox_to_none(aiohttp_client, upstreams):
    openai, groq = await upstreams("openai"), await upstreams("groq")
    client = await _proxy(
        aiohttp_client,
        provider_servers={"openai": _server(openai, "k-o", "cred-o"), "groq": _server(groq, "k-g", "cred-g")},
    )
    declared = await _declare(client)
    assert declared.status == 200 and "provider" not in await declared.json()
    chat = {"model": "gpt-x", "messages": []}
    assert (await client.post("/p/openai/v1/chat/completions", json=chat, headers=_BEARER)).status == 200
    assert (await client.post("/p/groq/v1/chat/completions", json=chat, headers=_BEARER)).status == 200


@pytest.mark.parametrize(
    "provider", ["", "OpenAI", "open ai", 7, "x" * 40], ids=["empty", "upper", "space", "int", "long"]
)
async def test_a_declaration_naming_a_provider_that_is_not_one_is_refused(aiohttp_client, upstreams, provider):
    openai = await upstreams("openai")
    client = await _proxy(aiohttp_client, provider_servers={"openai": _server(openai, "k-o", "cred-o")})
    resp = await _declare(client, provider=provider)
    assert resp.status == 400, f"a declaration on {provider!r} was taken: {resp.status}"


async def test_a_run_declared_again_on_another_provider_is_refused(aiohttp_client, upstreams):
    openai = await upstreams("openai")
    client = await _proxy(aiohttp_client, provider_servers={"openai": _server(openai, "k-o", "cred-o")})
    assert (await _declare(client, provider="openai")).status == 200
    assert (await _declare(client, provider="openai")).status == 200, "the same declaration again was refused"
    again = await _declare(client, provider="groq")
    assert again.status == 409, f"the run moved to another provider: {again.status}"


# --- what goes back to the sandbox names no key ----------------------------------------------------------------------

_KEY = "sk-proj-key-the-sandbox-never-holds-0123456789"


async def _echoing(aiohttp_server: Any, answer: Any) -> Any:
    """A provider's server answering every chat completion with ``answer(request)``."""
    app = web.Application()
    app.router.add_post("/v1/chat/completions", answer)
    return await aiohttp_server(app)


async def test_an_error_answer_quoting_the_key_reaches_the_sandbox_without_it(aiohttp_client, aiohttp_server):
    async def refuse(request: web.Request) -> web.Response:
        quoted = request.headers.get("Authorization", "").removeprefix("Bearer ")
        message = f"Incorrect API key provided: {quoted}. You can find your API key at the provider's site."
        return web.json_response({"error": {"message": message, "code": "invalid_api_key"}}, status=401)

    upstream = await _echoing(aiohttp_server, refuse)
    client = await _proxy(aiohttp_client, provider_servers={"openai": _server(upstream, _KEY, "cred-o")})
    resp = await client.post("/p/openai/v1/chat/completions", json={"model": "gpt-x", "messages": []}, headers=_BEARER)
    body = await resp.read()
    assert resp.status == 401
    assert _KEY.encode() not in body, f"the provider's key reached the sandbox: {body!r}"
    assert b"Incorrect API key provided: ***REDACTED***" in body, f"the rest of the answer was lost: {body!r}"


@pytest.mark.parametrize("split", [1, 9, len(_KEY) - 1], ids=["first-byte", "middle", "last-byte"])
async def test_a_key_split_across_streamed_chunks_is_still_scrubbed(aiohttp_client, aiohttp_server, split):
    async def stream(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        key = request.headers.get("Authorization", "").removeprefix("Bearer ").encode()
        for part in (b'data: {"said": "' + key[:split], key[split:] + b'"}\n\n', b"data: [DONE]\n\n"):
            await response.write(part)  # one chunk each: the write waits for it to go
        await response.write_eof()
        return response

    upstream = await _echoing(aiohttp_server, stream)
    client = await _proxy(aiohttp_client, provider_servers={"openai": _server(upstream, _KEY, "cred-o")})
    chat = {"model": "gpt-x", "messages": [], "stream": True}
    resp = await client.post("/p/openai/v1/chat/completions", json=chat, headers=_BEARER)
    body = await resp.read()
    assert _KEY.encode() not in body, f"a key split across the stream's chunks reached the sandbox: {body!r}"
    assert body == b'data: {"said": "***REDACTED***"}\n\ndata: [DONE]\n\n', body


async def test_another_sandbox_s_key_is_scrubbed_too(aiohttp_client, aiohttp_server):
    """A server two credentials share could answer one sandbox with the other's key."""
    other = "sk-another-credential-s-key-9876543210"

    async def leak(request: web.Request) -> web.Response:
        return web.json_response({"choices": [{"message": {"content": f"the last key I saw: {other}"}}]})

    upstream = await _echoing(aiohttp_server, leak)
    client = await _proxy(aiohttp_client, provider_servers={"openai": _server(upstream, _KEY, "cred-o")})
    second = {
        "sandbox_id": "sbx-2",
        "sandbox_token": "another-sandbox-bearer-token-0123456789-abcdef",
        "limits": LIMITS,
        "provider_servers": {"openai": _server(upstream, other, "cred-other")},
    }
    assert (await client.post("/admin/enroll", json=second, headers=_H)).status == 200
    resp = await client.post("/p/openai/v1/chat/completions", json={"model": "gpt-x", "messages": []}, headers=_BEARER)
    body = await resp.read()
    assert other.encode() not in body, f"another sandbox's key reached this one: {body!r}"


def test_the_scrubber_finds_a_key_split_at_any_byte_of_any_chunking() -> None:
    """The chunks an answer arrives in are the network's: a key may be split at any byte, over several chunks."""
    scrubber_type = getattr(llm_proxy, "_Scrubber", None)
    assert scrubber_type is not None, "nothing scrubs a key from what goes back to the sandbox"
    text = b'data: {"said": "' + _KEY.encode() + b'", "and again": "' + _KEY.encode() + b'", "a short": 1}\n\n'
    expected = text.replace(_KEY.encode(), b"***REDACTED***")
    for size in (1, 2, 7, len(_KEY) - 1, len(_KEY), len(_KEY) + 1, len(text)):
        # A key that is the start of another (two credentials' keys) is replaced inside the longer one too.
        scrubber = scrubber_type([_KEY[:20], _KEY, "short"])
        out = b"".join(scrubber.feed(text[i : i + size]) for i in range(0, len(text), size)) + scrubber.finish()
        assert out == expected, f"chunks of {size} bytes went back as {out!r}"


async def test_the_meter_reads_the_answer_as_the_provider_sent_it(aiohttp_client, aiohttp_server):
    """The scrub holds the last bytes of each chunk back, and replaces a key: the usage is read from the provider's
    own bytes, whole, all the same."""
    recorded = (ANSWERS / "openai-chat.json").read_bytes()

    async def answer(request: web.Request) -> web.Response:
        return web.Response(body=recorded, content_type="application/json")

    upstream = await _echoing(aiohttp_server, answer)
    client = await _proxy(aiohttp_client, provider_servers={"openai": _server(upstream, _KEY, "cred-o")})
    assert (await _declare(client, provider="openai")).status == 200
    call = await client.post("/p/openai/v1/chat/completions", json={"model": "gpt-x", "messages": []}, headers=_BEARER)
    assert call.status == 200 and await call.read() == recorded, "the answer did not go back as it came"
    meter = await (await client.get("/admin/meter", headers=_H)).json()
    tally = next(t for t in meter["tallies"] if t.get("run_id") == "run-1")
    counted = next(iter(tally["models"].values()))
    assert (
        counted["input_tokens"] + counted.get("cache_read_tokens", 0),
        counted["output_tokens"],
        counted["estimated"],
    ) == (
        1200,
        80,
        0,
    ), f"the usage was not read from the answer whole: {tally}"
