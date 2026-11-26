"""The local-llm-proxy forwards an enrolled sandbox's model calls to the server it was enrolled with, and nothing else
(ADR 0032)."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient

from embry0.execution.proxy import local_llm_proxy
from tests.fixtures.model_servers import allow_loopback_test_servers
from tests.fixtures.recording_upstream import is_test_request

_ADMIN = "test-admin-secret-not-real"
_SANDBOX_TOKEN = "sandbox-bearer-token-at-least-32-chars-long"
_SERVER_KEY = "server-key-the-sandbox-never-sees"
_API_ROOT = "/v1/"

#: The limits every enrollment carries (the settings document's local_models; the section's defaults here).
LIMITS: dict[str, Any] = {"max_request_bytes": 8 * 1024 * 1024, "timeout_seconds": 900.0}


@pytest.fixture(autouse=True)
def _fake_servers_on_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake model servers here listen on 127.0.0.1, which no real enrollment may name (ADR 0032): these tests
    let that one address through, and every other refused host stays refused
    (tests/unit/execution/proxy/test_model_server_hosts.py refuses 127.0.0.1 itself)."""
    allow_loopback_test_servers(monkeypatch)


@pytest.fixture
def make_upstream(aiohttp_server) -> Any:
    """A factory of fake model servers recording the requests the proxy may send them.

    ``seen`` holds the requests under the API root the proxy forwards to, and
    any carrying the server key, wherever they go. Anything else that reaches
    its port, such as an unrelated client's ``GET /``, is answered the same way
    and kept in ``ignored`` (#221). Each server answers with its own ``name``.
    """

    async def make(name: str = "one") -> Any:
        seen: list[dict[str, Any]] = []
        ignored: list[dict[str, Any]] = []

        async def record(request: web.Request) -> web.Response:
            counted = is_test_request(request.path, request.headers, paths=[_API_ROOT], credentials=[_SERVER_KEY])
            (seen if counted else ignored).append(
                {
                    "method": request.method,
                    "path": request.path,
                    "authorization": request.headers.get("Authorization"),
                    "host": request.headers.get("Host"),
                    "body": await request.read(),
                }
            )
            if request.path.endswith("/models"):
                return web.json_response({"data": [{"id": "qwen", "max_model_len": 65536}]})
            return web.json_response({"choices": [{"message": {"role": "assistant", "content": name}}]})

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", record)
        server = await aiohttp_server(app)
        server.seen = seen
        server.ignored = ignored
        return server

    return make


@pytest.fixture
async def upstream(make_upstream) -> Any:
    return await make_upstream()


def _server(
    upstream: Any, *, api_key: str = "", credential_id: str = "cred-a", name: str = "127.0.0.1"
) -> dict[str, Any]:
    """An enrollment's server: the fake server's root under ``name``, and the address it was checked at."""
    return {
        "credential_id": credential_id,
        "upstream": f"http://{name}:{upstream.port}/v1/",
        "api_key": api_key,
        "address": "127.0.0.1",
    }


async def _enroll(client: TestClient, sandbox_id: str, token: str, server: Any, **limits: Any) -> Any:
    """Enroll ``sandbox_id`` with ``server`` and the sandbox's limits (``limits`` over the section's defaults; a None
    value leaves its key out)."""
    sent = {k: v for k, v in {**LIMITS, **limits}.items() if v is not None}
    return await client.post(
        "/admin/enroll",
        json={"sandbox_id": sandbox_id, "sandbox_token": token, "local_llm": server, "limits": sent},
        headers={"X-Admin-Token": _ADMIN},
    )


def create_local_llm_proxy_app(admin_token: str, **kwargs: Any) -> web.Application:
    """The proxy app, built as ADR 0032 builds it: with the admin token and no server of its own."""
    try:
        return local_llm_proxy.create_local_llm_proxy_app(admin_token, **kwargs)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and (not admin_token or kwargs.get("max_request_bytes") == 0):
            raise
        pytest.fail(f"the local-llm-proxy must be built with no server of its own (ADR 0032): {exc}")


async def _proxy(
    aiohttp_client, upstream: Any, *, api_key: str = "", limits: dict[str, Any] | None = None, **kwargs: Any
) -> TestClient:
    client = await aiohttp_client(create_local_llm_proxy_app(_ADMIN, **kwargs))
    enrolled = await _enroll(client, "sbx-1", _SANDBOX_TOKEN, _server(upstream, api_key=api_key), **(limits or {}))
    assert enrolled.status == 200, await enrolled.text()
    return client


_AUTH = {"Authorization": f"Bearer {_SANDBOX_TOKEN}"}


def test_the_admin_token_and_a_positive_cap_must_be_set():
    with pytest.raises(ValueError):
        create_local_llm_proxy_app("")
    with pytest.raises(ValueError):
        create_local_llm_proxy_app(_ADMIN, max_request_bytes=0)


@pytest.mark.parametrize(
    "server",
    [
        None,
        "http://10.0.0.5:11434/v1",
        {"credential_id": "cred-a", "api_key": "k"},
        {"credential_id": "cred-a", "upstream": "file:///etc/passwd", "api_key": "", "address": "10.0.0.5"},
        {"credential_id": "cred-a", "upstream": "ftp://10.0.0.5/v1", "api_key": "", "address": "10.0.0.5"},
        {"credential_id": "cred-a", "upstream": "http:///v1", "api_key": "", "address": "10.0.0.5"},
        {
            "credential_id": "cred-a",
            "upstream": "http://user:pw@10.0.0.5/v1",
            "api_key": "",
            "address": "10.0.0.5",
        },
        {
            "credential_id": "cred-a",
            "upstream": "http://10.0.0.5/v1?x=1",
            "api_key": "",
            "address": "10.0.0.5",
        },
        {
            "credential_id": "cred-a",
            "upstream": "http://10.0.0.5/v1",
            "api_key": "k\r\nX-Evil: 1",
            "address": "10.0.0.5",
        },
        {"credential_id": 7, "upstream": "http://10.0.0.5/v1", "api_key": "", "address": "10.0.0.5"},
        {
            "credential_id": "cred-a",
            "upstream": "http://127.0.0.2:8000/v1",
            "api_key": "",
            "address": "10.0.0.5",
        },
        {"credential_id": "cred-a", "upstream": "http://[::1]:8000/v1", "api_key": "", "address": "10.0.0.5"},
        {
            "credential_id": "cred-a",
            "upstream": "http://localhost:8000/v1",
            "api_key": "",
            "address": "10.0.0.5",
        },
        {
            "credential_id": "cred-a",
            "upstream": "http://169.254.169.254/latest",
            "api_key": "",
            "address": "10.0.0.5",
        },
        {
            "credential_id": "cred-a",
            "upstream": "http://[fe80::1]:8000/v1",
            "api_key": "",
            "address": "10.0.0.5",
        },
        {
            "credential_id": "cred-a",
            "upstream": "http://0.0.0.0:8000/v1",
            "api_key": "",
            "address": "10.0.0.5",
        },
        {
            "credential_id": "cred-a",
            "upstream": "http://2130706434:8000/v1",
            "api_key": "",
            "address": "10.0.0.5",
        },
        {"credential_id": "cred-a", "upstream": "http://0xa9fea9fe/v1", "api_key": "", "address": "10.0.0.5"},
        {"credential_id": "cred-a", "upstream": "http://models.test/v1", "api_key": ""},
        {
            "credential_id": "cred-a",
            "upstream": "http://models.test/v1",
            "api_key": "",
            "address": "models.test",
        },
        {
            "credential_id": "cred-a",
            "upstream": "http://models.test/v1",
            "api_key": "",
            "address": "2130706433",
        },
        {
            "credential_id": "cred-a",
            "upstream": "http://models.test/v1",
            "api_key": "",
            "address": "127.0.0.2",
        },
        {
            "credential_id": "cred-a",
            "upstream": "http://models.test/v1",
            "api_key": "",
            "address": "169.254.169.254",
        },
        {
            "credential_id": "cred-a",
            "upstream": "http://models.test/v1",
            "api_key": "",
            "address": "::ffff:127.0.0.3",
        },
    ],
    ids=[
        "none",
        "a-string",
        "no-upstream",
        "not-http",
        "ftp-with-host",
        "no-host",
        "credentials",
        "query",
        "header-break",
        "bad-id",
        "loopback",
        "loopback-v6",
        "localhost",
        "metadata-service",
        "link-local-v6",
        "unspecified",
        "loopback-as-a-number",
        "metadata-in-hex",
        "no-address",
        "address-a-name",
        "address-a-number",
        "address-loopback",
        "address-metadata",
        "address-mapped-loopback",
    ],
)
async def test_an_enrollment_without_a_valid_server_is_refused(aiohttp_client, server):
    """Every sandbox is enrolled with its server; one that names none, or one the proxy cannot safely reach, gets no
    bearer at all."""
    client = await aiohttp_client(create_local_llm_proxy_app(_ADMIN))
    body: dict[str, Any] = {"sandbox_id": "sbx-1", "sandbox_token": _SANDBOX_TOKEN}
    if server is not None:
        body["local_llm"] = server
    refused = await client.post("/admin/enroll", json=body, headers={"X-Admin-Token": _ADMIN})
    assert refused.status == 400, f"an enrollment naming {server!r} was taken: {refused.status}"
    assert (await client.post("/v1/chat/completions", json={}, headers=_AUTH)).status == 401, (
        "a refused enrollment left a bearer the proxy accepts"
    )


async def test_the_enrollment_answer_confirms_the_server_and_never_the_key(aiohttp_client, upstream):
    client = await aiohttp_client(create_local_llm_proxy_app(_ADMIN))
    enrolled = await _enroll(client, "sbx-1", _SANDBOX_TOKEN, _server(upstream, api_key=_SERVER_KEY))
    answer = await enrolled.json()
    assert answer["local_llm"] == {
        "credential_id": "cred-a",
        "upstream": f"http://127.0.0.1:{upstream.port}/v1",
        "address": "127.0.0.1",
    }, f"the answer must echo the server, without its trailing slash: {answer}"
    assert answer["limits"] == LIMITS, f"the answer must echo the sandbox's limits: {answer}"
    assert _SERVER_KEY not in await enrolled.text(), "the enrollment answer carried the server's key"


async def test_each_sandbox_reaches_its_own_server_only(aiohttp_client, make_upstream):
    """Two jobs on two custom credentials: each sandbox's calls go to its credential's server, with its key."""
    one, two = await make_upstream("one"), await make_upstream("two")
    client = await aiohttp_client(create_local_llm_proxy_app(_ADMIN))
    other_token = "another-sandbox-bearer-at-least-32-chars"
    assert (await _enroll(client, "sbx-1", _SANDBOX_TOKEN, _server(one, api_key=_SERVER_KEY))).status == 200
    assert (await _enroll(client, "sbx-2", other_token, _server(two, credential_id="cred-b"))).status == 200

    first = await client.post("/v1/chat/completions", json={"messages": []}, headers=_AUTH)
    second = await client.post(
        "/v1/chat/completions", json={"messages": []}, headers={"Authorization": f"Bearer {other_token}"}
    )
    answers = [(await r.json())["choices"][0]["message"]["content"] for r in (first, second)]
    assert answers == ["one", "two"], f"each sandbox must reach its own server: {answers}"
    assert [r["authorization"] for r in one.seen] == [f"Bearer {_SERVER_KEY}"], (
        f"the first server got other than its own key: {one.seen}"
    )
    assert [r["authorization"] for r in two.seen] == [None], (
        f"the second server, whose credential has no key, got one: {two.seen}"
    )


async def test_an_enrollment_again_moves_the_sandbox_to_its_new_server(aiohttp_client, make_upstream):
    one, two = await make_upstream("one"), await make_upstream("two")
    client = await _proxy(aiohttp_client, one)
    assert (await _enroll(client, "sbx-1", _SANDBOX_TOKEN, _server(two, credential_id="cred-b"))).status == 200
    resp = await client.post("/v1/chat/completions", json={"messages": []}, headers=_AUTH)
    assert (await resp.json())["choices"][0]["message"]["content"] == "two", "the sandbox kept its first server"
    assert one.seen == [], f"the first server was still reached: {one.seen}"


async def test_a_sandbox_whose_server_is_gone_is_answered_503(aiohttp_client, upstream):
    """An enrolled bearer always has its server; were the two ever apart, nothing would be forwarded anywhere."""
    client = await _proxy(aiohttp_client, upstream)
    held = getattr(local_llm_proxy, "UPSTREAM_BY_SANDBOX", None)
    assert held is not None, "the proxy keeps no server per sandbox (UPSTREAM_BY_SANDBOX)"
    client.server.app[held].clear()
    resp = await client.post("/v1/chat/completions", json={}, headers=_AUTH)
    assert resp.status == 503, f"a sandbox with no server was answered {resp.status}"
    assert upstream.seen == []


async def test_a_call_connects_to_the_checked_address_and_names_the_server_in_host(aiohttp_client, upstream):
    """The orchestrator resolved and checked the server's name; the proxy connects to that address, never to the
    name again (no second lookup to answer otherwise), and the server still reads its own name."""
    client = await aiohttp_client(create_local_llm_proxy_app(_ADMIN))
    server = _server(upstream, name="models.test")
    assert (await _enroll(client, "sbx-1", _SANDBOX_TOKEN, server)).status == 200
    resp = await client.post("/v1/chat/completions", json={"messages": []}, headers=_AUTH)
    assert resp.status == 200, f"the call did not reach the checked address: {resp.status} {await resp.text()}"
    assert [r["host"] for r in upstream.seen] == [f"models.test:{upstream.port}"], (
        f"the server must be named in Host, as the credential names it: {upstream.seen}"
    )


async def test_an_https_server_keeps_its_name_for_tls(aiohttp_client, aiohttp_server, tmp_path, monkeypatch):
    """Connecting to the address leaves the certificate to be checked for the name: the call goes to the
    credential's URL, its name the Host and the TLS server name, over a connection to the checked address."""
    from tests.fixtures.tls_servers import tls_server

    tls = await tls_server(aiohttp_server, tmp_path, monkeypatch, "models.test")
    client = await aiohttp_client(create_local_llm_proxy_app(_ADMIN))
    server = {
        "credential_id": "cred-a",
        "upstream": f"https://models.test:{tls.port}/v1",
        "api_key": "",
        "address": "127.0.0.1",
    }
    assert (await _enroll(client, "sbx-1", _SANDBOX_TOKEN, server)).status == 200
    resp = await client.post("/v1/chat/completions", json={"messages": []}, headers=_AUTH)
    assert resp.status == 200, await resp.text()
    assert [seen["host"] for seen in tls.seen] == [f"models.test:{tls.port}"], (
        f"the server's own name, in Host: {tls.seen}"
    )
    assert tls.handshakes == ["models.test"], (
        f"the TLS server name must be the credential's, so its certificate is checked for it: {tls.handshakes}"
    )


async def test_chat_completions_and_models_reach_the_sandbox_s_server(aiohttp_client, upstream):
    client = await _proxy(aiohttp_client, upstream, api_key=_SERVER_KEY)
    chat = await client.post("/v1/chat/completions", json={"model": "qwen", "messages": []}, headers=_AUTH)
    assert chat.status == 200
    assert (await chat.json())["choices"][0]["message"]["content"] == "one"
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
    held = getattr(local_llm_proxy, "UPSTREAM_BY_SANDBOX", None)
    assert held is not None and "sbx-1" not in client.server.app[held], (
        "a revoked sandbox's server, and its key, stayed in the proxy's memory"
    )


async def test_a_request_over_the_size_limit_is_refused(aiohttp_client, upstream):
    client = await _proxy(aiohttp_client, upstream, limits={"max_request_bytes": 1024})
    resp = await client.post(
        "/v1/chat/completions", data=b"x" * 2048, headers={**_AUTH, "Content-Type": "application/json"}
    )
    assert resp.status == 413
    error = (await resp.json())["error"]
    # The OpenAI error shape the chat-completions executor reads, saying what classifies as a context overflow.
    assert error["code"] == "request_too_large" and error["message"].startswith("request_too_large"), error
    assert "1024" in error["message"] and "local_models.max_request_bytes" in error["message"], error
    assert upstream.seen == []
    ok = await client.post("/v1/chat/completions", data=b"{}", headers={**_AUTH, "Content-Type": "application/json"})
    assert ok.status == 200


async def test_the_executor_reads_the_proxy_s_413_as_an_outgrown_conversation(aiohttp_client, upstream):
    """The refusal reaches the chat-completions executor as a provider error; the run ends as
    ERR_CONTEXT_WINDOW_EXCEEDED, not as an unexplained upstream failure."""
    import httpx

    from embry0.agents.executor_openai import ProviderHTTPError, _error_detail
    from embry0.safety.error_codes import ErrorCode

    client = await _proxy(aiohttp_client, upstream, limits={"max_request_bytes": 1024})
    resp = await client.post(
        "/v1/chat/completions", data=b"x" * 2048, headers={**_AUTH, "Content-Type": "application/json"}
    )
    answered = httpx.Response(resp.status, content=await resp.read(), headers={"content-type": "application/json"})
    failure = ProviderHTTPError("custom", answered.status_code, _error_detail(answered))
    assert failure.error_code == ErrorCode.CONTEXT_WINDOW_EXCEEDED, f"the run would end as {failure.error_code}"


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
    client = await _proxy(aiohttp_client, upstream, api_key=_SERVER_KEY)
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
    client = await aiohttp_client(create_local_llm_proxy_app(_ADMIN))
    dead = {
        "credential_id": "cred-a",
        "upstream": "http://127.0.0.1:9/v1",
        "api_key": "",
        "address": "127.0.0.1",
    }
    assert (await _enroll(client, "sbx-1", _SANDBOX_TOKEN, dead)).status == 200
    resp = await client.post("/v1/chat/completions", json={}, headers=_AUTH)
    assert resp.status == 502


# --- #174: each sandbox's limits are its enrollment's, under the ceilings the proxy starts with -----------------


_TOKEN_1, _TOKEN_2 = (f"sandbox-bearer-token-{n}-at-least-32-chars-long" for n in (1, 2))


async def test_each_sandbox_has_the_request_cap_it_was_enrolled_with(aiohttp_client, upstream):
    """The cap is the settings document's local_models.max_request_bytes, sent with each enrollment: a change
    reaches the next sandbox while the proxy keeps running."""
    client = await aiohttp_client(create_local_llm_proxy_app(_ADMIN))
    for sandbox, token, cap in (("sbx-1", _TOKEN_1, 1024), ("sbx-2", _TOKEN_2, 4096)):
        enrolled = await _enroll(client, sandbox, token, _server(upstream), max_request_bytes=cap)
        assert enrolled.status == 200, await enrolled.text()
    body = b"x" * 2048
    small = await client.post(
        "/v1/chat/completions", data=body, headers={"Authorization": f"Bearer {_TOKEN_1}", "Content-Type": "text/plain"}
    )
    large = await client.post(
        "/v1/chat/completions", data=body, headers={"Authorization": f"Bearer {_TOKEN_2}", "Content-Type": "text/plain"}
    )
    assert (small.status, large.status) == (413, 200), (
        f"each sandbox's cap is its own enrollment's: 1024 bytes -> {small.status}, 4096 bytes -> {large.status}"
    )
    assert "1024-byte cap" in (await small.json())["error"]["message"]


@pytest.mark.parametrize(
    ("limits", "named"),
    [
        ({"max_request_bytes": None}, "max_request_bytes"),
        ({"timeout_seconds": None}, "timeout_seconds"),
        ({"max_request_bytes": 0}, "max_request_bytes"),
        ({"max_request_bytes": -1}, "max_request_bytes"),
        ({"max_request_bytes": 8193}, "max_request_bytes"),  # past the ceiling this proxy started with
        ({"max_request_bytes": True}, "max_request_bytes"),
        ({"max_request_bytes": 1024.5}, "max_request_bytes"),
        ({"max_request_bytes": "1024"}, "max_request_bytes"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"timeout_seconds": 3600.5}, "timeout_seconds"),
        ({"timeout_seconds": False}, "timeout_seconds"),
        ({"timeout_seconds": "900"}, "timeout_seconds"),
    ],
)
async def test_an_enrollment_without_its_limits_or_past_a_ceiling_is_refused(aiohttp_client, upstream, limits, named):
    client = await aiohttp_client(create_local_llm_proxy_app(_ADMIN, max_request_bytes=8192))
    resp = await _enroll(client, "sbx-1", _SANDBOX_TOKEN, _server(upstream), **{"max_request_bytes": 4096, **limits})
    assert resp.status == 400, f"an enrollment with {limits} was taken: {await resp.text()}"
    assert f"limits.{named}" in await resp.text(), await resp.text()
    assert "sbx-1" not in client.server.app[local_llm_proxy.UPSTREAM_BY_SANDBOX]


async def test_an_enrollment_at_the_ceilings_is_taken_and_echoed(aiohttp_client, upstream):
    """The ceilings themselves are allowed; the echo, which the orchestrator compares with what it sent, carries the
    limits the proxy holds."""
    client = await aiohttp_client(create_local_llm_proxy_app(_ADMIN, max_request_bytes=8192))
    resp = await _enroll(
        client, "sbx-1", _SANDBOX_TOKEN, _server(upstream), max_request_bytes=8192, timeout_seconds=3600
    )
    assert resp.status == 200, await resp.text()
    echoed = (await resp.json())["limits"]
    assert (echoed["max_request_bytes"], echoed["timeout_seconds"]) == (8192, 3600.0), echoed
    held = client.server.app[local_llm_proxy.LIMITS_BY_SANDBOX]["sbx-1"]
    assert (held.max_request_bytes, held.timeout_seconds) == (8192, 3600.0), held


def test_the_proxy_starts_with_the_ceiling_the_settings_are_bounded_by():
    """With no MAX_REQUEST_BYTES of its own the proxy reads up to the ceiling, so any cap local_models allows can be
    enrolled; the section's bounds and the proxy's are one pair of numbers."""
    from embry0.settings.sections import local_models

    app = create_local_llm_proxy_app(_ADMIN)
    assert app[local_llm_proxy.MAX_REQUEST_BYTES] == local_llm_proxy.MAX_REQUEST_BYTES_CEILING
    assert local_models.MAX_REQUEST_BYTES_CEILING == local_llm_proxy.MAX_REQUEST_BYTES_CEILING
    assert local_models.TIMEOUT_SECONDS_CEILING == local_llm_proxy.TIMEOUT_SECONDS_CEILING
    fields = local_models.LocalModelsSettings.model_json_schema()["properties"]
    assert fields["max_request_bytes"]["maximum"] == local_llm_proxy.MAX_REQUEST_BYTES_CEILING, fields
    assert fields["request_timeout_seconds"]["maximum"] == local_llm_proxy.TIMEOUT_SECONDS_CEILING, fields
    defaults = local_models.LocalModelsSettings()
    assert defaults.max_request_bytes == local_llm_proxy.DEFAULT_MAX_REQUEST_BYTES
    assert defaults.request_timeout_seconds == local_llm_proxy.DEFAULT_TIMEOUT_SECONDS


async def test_a_server_has_the_enrollment_s_timeout_to_answer(aiohttp_client, aiohttp_server):
    """local_models.request_timeout_seconds, per enrollment: a server slower than a sandbox's timeout is a 504 for
    that sandbox, and answers another sandbox that allows it longer."""

    async def slow(_request: web.Request) -> web.Response:
        await asyncio.sleep(0.6)
        return web.json_response({"answer": "late"})

    app = web.Application()
    app.router.add_post("/v1/chat/completions", slow)
    server = await aiohttp_server(app)
    client = await aiohttp_client(create_local_llm_proxy_app(_ADMIN))
    for sandbox, token, timeout in (("sbx-1", _TOKEN_1, 0.2), ("sbx-2", _TOKEN_2, 5)):
        enrolled = await _enroll(client, sandbox, token, _server(server), timeout_seconds=timeout)
        assert enrolled.status == 200, await enrolled.text()
    impatient = await client.post("/v1/chat/completions", json={}, headers={"Authorization": f"Bearer {_TOKEN_1}"})
    patient = await client.post("/v1/chat/completions", json={}, headers={"Authorization": f"Bearer {_TOKEN_2}"})
    assert (impatient.status, patient.status) == (504, 200), (
        f"a 0.2 s timeout -> {impatient.status}, a 5 s one -> {patient.status}: the enrollment's timeout must bound "
        "the server's answer"
    )
