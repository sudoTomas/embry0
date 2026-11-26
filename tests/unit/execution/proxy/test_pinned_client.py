"""A call to an operator's model server connects to its checked address, keyed and verified by its own name.

The pool of a client that rewrote the URL to the address was keyed by the address: after one TLS handshake
verified for name A, a call for name B pinned to the same address went over A's connection, its certificate never
checked for B, and B's key with it. Each site that calls a model server (the local-llm-proxy, the background calls,
the probe behind the startup and credential checks) is tested here against one TLS server whose certificate holds
one name, reached under that name and under another, in both orders.
"""

from __future__ import annotations

import importlib
from typing import Any

import httpx
import pytest
from aiohttp import web

from embry0.execution.proxy import local_llm_proxy
from tests.fixtures.model_servers import allow_loopback_test_servers
from tests.fixtures.tls_servers import TlsServer, tls_server

RIGHT, WRONG = "models.test", "other.test"
_ADMIN = "test-admin-secret-not-real"
_TOKENS = {RIGHT: "sandbox-bearer-token-right-at-least-32-chars", WRONG: "sandbox-bearer-token-wrong-at-least-32-chars"}
_KEYS = {RIGHT: "key-for-the-right-name", WRONG: "key-for-another-name"}


@pytest.fixture(autouse=True)
def _servers_on_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    allow_loopback_test_servers(monkeypatch)


@pytest.fixture
async def server(aiohttp_server: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> TlsServer:
    """A TLS server on 127.0.0.1 whose certificate holds models.test alone."""
    return await tls_server(aiohttp_server, tmp_path, monkeypatch, RIGHT)


def _pinned_client() -> Any:
    try:
        return importlib.import_module("embry0.execution.proxy.pinned_client")
    except ImportError:
        pytest.fail("no client keys a model server's connections by its name (execution.proxy.pinned_client)")


def _keys_received(server: TlsServer) -> list[str | None]:
    return [seen["authorization"] for seen in server.seen]


# --- the client ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "address", "host", "port"),
    [
        ("https://models.test:8443/v1/models", "10.0.0.7", "models.test", 8443),
        ("http://models.test/v1/models", "10.0.0.7", "models.test", 80),
        ("https://models.test/v1/models", "2001:db8::7", "models.test", 443),
        ("https://Models.Test/v1/models", "10.0.0.7", "models.test", 443),
        ("https://10.0.0.7/v1/models", "10.0.0.7", "10.0.0.7", 443),
    ],
)
async def test_a_pinned_call_connects_to_its_address_and_keeps_the_name(
    url: str, address: str, host: str, port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The socket goes to the address, at the URL's port; the request goes to the URL, its name for Host and TLS."""
    module = _pinned_client()
    opened: list[tuple[str, int]] = []
    sent: list[httpx.URL] = []

    class Inner:
        async def connect_tcp(self, host: str, port: int, **_kwargs: Any) -> Any:
            opened.append((host, port))
            raise module.UnpinnedConnection("stop here: the test needs the address only")

    async def keep(request: httpx.Request) -> None:
        sent.append(request.url)

    async with module.pinning_client(event_hooks={"request": [keep]}) as client:
        transport = client._transport  # noqa: SLF001 - the backend under test
        monkeypatch.setattr(transport._pool._network_backend, "_inner", Inner())  # noqa: SLF001
        with module.pinned(url, address), pytest.raises(httpx.ConnectError):
            await client.get(url)
    assert opened == [(address, port)], f"the connection went to {opened}, not {address}:{port}"
    assert [(u.host, u.port or port) for u in sent] == [(host, port)], f"the request lost its name: {sent}"


@pytest.mark.parametrize(
    "pin",
    [None, ("https://another.test/v1", "127.0.0.1"), ("https://models.test:8443/v1", "127.0.0.1")],
    ids=["no-pin", "another-host", "another-port"],
)
async def test_a_connection_with_no_pin_for_its_host_and_port_is_refused(pin: Any, aiohttp_server: Any) -> None:
    """The client connects nowhere by a lookup of its own: a call made outside ``pinned``, or pinned for another
    server, opens no connection."""
    module = _pinned_client()
    seen: list[str] = []

    async def answer(request: web.Request) -> web.Response:
        seen.append(request.path)
        return web.json_response({})

    app = web.Application()
    app.router.add_get("/{tail:.*}", answer)
    target = await aiohttp_server(app)
    url = f"http://127.0.0.1:{target.port}/v1/models"
    async with module.pinning_client() as client:
        if pin is None:
            with pytest.raises(httpx.ConnectError, match="no checked address"):
                await client.get(url)
        else:
            with module.pinned(*pin), pytest.raises(httpx.ConnectError, match="no checked address"):
                await client.get(url)
        with module.pinned(url, "127.0.0.1"):
            assert (await client.get(url)).status_code == 200, "a pinned call to the same server was refused"
    # The pin ends with its block: a new connection after it is refused (a fresh client, with no connection kept).
    async with module.pinning_client() as fresh:
        with pytest.raises(httpx.ConnectError, match="no checked address"):
            await fresh.get(url)
    assert seen == ["/v1/models"], f"an unpinned call reached the server: {seen}"


async def test_a_name_is_sent_as_the_host_and_never_looked_up(aiohttp_server: Any) -> None:
    hosts: list[str | None] = []

    async def answer(request: web.Request) -> web.Response:
        hosts.append(request.headers.get("Host"))
        return web.json_response({})

    app = web.Application()
    app.router.add_get("/{tail:.*}", answer)
    target = await aiohttp_server(app)
    module = _pinned_client()
    url = f"http://gpu-box.invalid:{target.port}/v1/models"  # .invalid resolves nowhere
    async with module.pinning_client() as client:
        with module.pinned(url, "127.0.0.1"):
            assert (await client.get(url)).status_code == 200
    assert hosts == [f"gpu-box.invalid:{target.port}"], hosts


@pytest.mark.parametrize("order", [(RIGHT, WRONG), (WRONG, RIGHT, WRONG)], ids=["right-first", "wrong-first"])
async def test_one_client_checks_each_name_on_a_connection_of_its_own(
    server: TlsServer, order: tuple[str, ...]
) -> None:
    """The reviewer's probe on the client itself: one client, one address, two names."""
    module = _pinned_client()
    outcomes = []
    async with module.pinning_client() as client:
        for name in order:
            url = f"https://{name}:{server.port}/v1/models"
            try:
                with module.pinned(url, "127.0.0.1"):
                    await client.get(url, headers={"Authorization": f"Bearer {_KEYS[name]}"})
                outcomes.append((name, "answered"))
            except httpx.ConnectError:
                outcomes.append((name, "refused"))
    assert outcomes == [(n, "answered" if n == RIGHT else "refused") for n in order], outcomes
    assert _keys_received(server) == [f"Bearer {_KEYS[RIGHT]}"], "a key went over a connection checked for another name"


# --- the local-llm-proxy ------------------------------------------------------------------------------------------


async def _enroll(client: Any, name: str, port: int) -> None:
    server = {"credential_id": f"cred-{name}", "upstream": f"https://{name}:{port}/v1", "api_key": _KEYS[name]}
    resp = await client.post(
        "/admin/enroll",
        json={
            "sandbox_id": "sbx-right" if name == RIGHT else "sbx-wrong",
            "sandbox_token": _TOKENS[name],
            "local_llm": {**server, "address": "127.0.0.1"},
        },
        headers={"X-Admin-Token": _ADMIN},
    )
    assert resp.status == 200, await resp.text()


@pytest.mark.parametrize("order", [(RIGHT, WRONG), (WRONG, RIGHT, WRONG)], ids=["right-first", "wrong-first"])
async def test_the_proxy_never_sends_a_sandbox_s_key_over_another_name_s_connection(
    aiohttp_client: Any, server: TlsServer, order: tuple[str, ...]
) -> None:
    """Two sandboxes on two custom credentials, one server address, the certificate holding one name: the other's
    call is refused at its own handshake, whichever came first, and its key never reaches the server."""
    client = await aiohttp_client(local_llm_proxy.create_local_llm_proxy_app(_ADMIN))
    for name in (RIGHT, WRONG):
        await _enroll(client, name, server.port)
    statuses = []
    for name in order:
        resp = await client.post(
            "/v1/chat/completions", json={"messages": []}, headers={"Authorization": f"Bearer {_TOKENS[name]}"}
        )
        statuses.append((name, resp.status))
    assert statuses == [(n, 200 if n == RIGHT else 502) for n in order], (
        f"a call under a name the certificate does not hold was answered: {statuses}"
    )
    assert _keys_received(server) == [f"Bearer {_KEYS[RIGHT]}"], (
        f"another name's key reached the server: {_keys_received(server)}"
    )


# --- the background calls -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("order", [(RIGHT, WRONG), (WRONG, RIGHT, WRONG)], ids=["right-first", "wrong-first"])
async def test_a_background_call_never_rides_another_name_s_connection(
    server: TlsServer, order: tuple[str, ...]
) -> None:
    """The features hand BackgroundCalls one client for every call they make; a call to the operator's server is
    checked for its own name whatever that client holds."""
    from embry0.services.background_models import BackgroundCalls, Endpoint
    from tests.unit.services.test_background_calls import Gate, Spend

    calls = BackgroundCalls(resolver=None, spend=Spend(), gate=Gate())
    outcomes = []
    async with httpx.AsyncClient() as shared:
        for name in order:
            endpoint = Endpoint(
                model="custom/qwen",
                root=f"https://{name}:{server.port}/v1",
                key=_KEYS[name],
                upstream_model="qwen",
                local=True,
                server=f"cred-{name}",
                address="127.0.0.1",
            )
            try:
                await calls.ask("job_brief", endpoint, shared, repo=None, system="s", user="u", schema={}, name="j")
                outcomes.append((name, "answered"))
            except httpx.ConnectError:
                outcomes.append((name, "refused"))
    assert outcomes == [(n, "answered" if n == RIGHT else "refused") for n in order], outcomes
    assert _keys_received(server) == [f"Bearer {_KEYS[RIGHT]}"], _keys_received(server)


# --- the probe behind the startup and credential checks -------------------------------------------------------------


@pytest.mark.parametrize("order", [(RIGHT, WRONG), (WRONG, RIGHT, WRONG)], ids=["right-first", "wrong-first"])
async def test_a_probe_checks_each_name_on_a_connection_of_its_own(server: TlsServer, order: tuple[str, ...]) -> None:
    from embry0.services import provider_probe

    statuses = []
    for name in order:
        check = await provider_probe._http_check(  # noqa: SLF001 - the one call both checks make
            "custom",
            f"https://{name}:{server.port}/v1/models",
            {"Authorization": f"Bearer {_KEYS[name]}"},
            address="127.0.0.1",
        )
        statuses.append((name, check.status))
    expected = [(n, provider_probe.STATUS_OK if n == RIGHT else provider_probe.STATUS_UNREACHABLE) for n in order]
    assert statuses == expected, statuses
    assert _keys_received(server) == [f"Bearer {_KEYS[RIGHT]}"], _keys_received(server)


def test_production_s_background_calls_pin_their_own_client() -> None:
    """``server_client`` defaults to a pinning client per call; production must not pass the caller's back (a test
    seam), or its calls would ride the features' shared clients again."""
    import ast
    import pathlib

    from embry0.api import app as app_module

    tree = ast.parse(pathlib.Path(app_module.__file__).read_text())
    built = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "BackgroundCalls"
    ]
    assert built, "embry0/api/app.py builds no BackgroundCalls"
    passed = [kw.arg for call in built for kw in call.keywords if kw.arg == "server_client"]
    assert passed == [], "production passes a server_client: its local calls would not be pinned per call"
