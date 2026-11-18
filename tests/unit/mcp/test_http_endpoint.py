"""Hosted MCP endpoint (embry0.mcp_server.http) — bearer gate + per-request forwarding."""

from __future__ import annotations

import httpx
import pytest

from embry0.mcp_server import server
from embry0.mcp_server.client import Embry0Client, caller_bearer
from embry0.mcp_server.http import BearerForwardMiddleware, KeyCheck, build_app, transport_security


def _check(answer: bool | None, asked: list[str] | None = None):
    """A key check that answers ``answer`` and records each bearer it is asked about."""

    async def check(bearer: str) -> bool | None:
        if asked is not None:
            asked.append(bearer)
        return answer

    return check


async def _never_asked(bearer: str) -> bool | None:
    raise AssertionError(f"the key check was asked about {bearer!r}")


async def _call(app, headers: list[tuple[bytes, bytes]]) -> tuple[int, bytes]:
    """Drive one ASGI http request; return (status, body)."""
    sent: dict[str, object] = {}
    body = b""

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        nonlocal body
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            body += message.get("body", b"")

    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": headers, "query_string": b""}
    await app(scope, receive, send)
    return int(sent["status"]), body


@pytest.mark.asyncio
async def test_anonymous_request_is_rejected_at_the_door():
    """The tool surface must not be enumerable without a key."""
    inner_called = False

    async def inner(scope, receive, send):
        nonlocal inner_called
        inner_called = True

    status, body = await _call(BearerForwardMiddleware(inner, _never_asked), headers=[])
    assert status == 401
    assert b"Authorization" in body
    assert inner_called is False


@pytest.mark.asyncio
async def test_empty_bearer_is_rejected():
    status, _ = await _call(BearerForwardMiddleware(None, _never_asked), headers=[(b"authorization", b"Bearer   ")])
    assert status == 401


@pytest.mark.asyncio
async def test_bearer_is_stashed_for_the_request_and_reset_after():
    seen: list[str | None] = []

    async def inner(scope, receive, send):
        seen.append(caller_bearer.get())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    asked: list[str] = []
    status, _ = await _call(
        BearerForwardMiddleware(inner, _check(True, asked)), headers=[(b"authorization", b"Bearer emb_caller-key")]
    )
    assert status == 200
    assert asked == ["emb_caller-key"], "the key is checked before anything is served"
    assert seen == ["emb_caller-key"]
    assert caller_bearer.get() is None  # reset — no bleed across requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "status", "body"),
    [
        (False, 401, b'{"error":"unauthorized","detail":"the embry0 API does not take this key"}'),
        (None, 503, b'{"error":"unavailable","detail":"the embry0 API could not check this key; try again"}'),
    ],
)
async def test_a_key_the_orchestrator_does_not_take_is_served_nothing(answer, status, body):
    """Discovery (initialize, the tool and prompt lists) is served only to a key the orchestrator takes."""
    inner_called = False

    async def inner(scope, receive, send):
        nonlocal inner_called
        inner_called = True

    got = await _call(BearerForwardMiddleware(inner, _check(answer)), headers=[(b"authorization", b"Bearer not-a-key")])
    assert got == (status, body)
    assert inner_called is False
    assert caller_bearer.get() is None


def _orchestrator_at(statuses: dict[str, int], seen: list[str | None]) -> Embry0Client:
    """A client over a fake orchestrator answering GET /api/v1/tools with the status its bearer maps to."""

    async def handler(request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("GET", "/api/v1/tools"), request.url
        bearer = (request.headers.get("Authorization") or "").removeprefix("Bearer ")
        seen.append(bearer)
        status = statuses.get(bearer, 401)
        if status == 0:
            raise httpx.ConnectError("refused")
        return httpx.Response(status, json=[] if status == 200 else {"detail": "no"})

    return Embry0Client("http://orchestrator.test", api_key="service-env-key", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_the_key_check_asks_the_orchestrator_with_the_callers_key(monkeypatch):
    seen: list[str | None] = []
    client = _orchestrator_at({"good": 200, "broken": 500, "down": 0}, seen)
    monkeypatch.setattr(server, "_client", client)
    check = KeyCheck()
    try:
        answers = {key: await check(key) for key in ("good", "bogus", "broken", "down")}
    finally:
        await client.aclose()
    assert answers == {"good": True, "bogus": False, "broken": None, "down": None}, answers
    assert seen == ["good", "bogus", "broken", "down"], "each asked with the caller's key, never the service's"
    assert caller_bearer.get() is None


@pytest.mark.asyncio
async def test_a_taken_key_is_kept_for_its_time_and_a_refused_one_never(monkeypatch):
    seen: list[str | None] = []
    client = _orchestrator_at({"good": 200}, seen)
    monkeypatch.setattr(server, "_client", client)
    now = [100.0]
    check = KeyCheck(ttl=30.0, clock=lambda: now[0])
    try:
        assert [await check("good"), await check("good")] == [True, True]
        assert [await check("bogus"), await check("bogus")] == [False, False]
        now[0] += 30.0
        assert await check("good") is True
    finally:
        await client.aclose()
    assert seen == ["good", "bogus", "bogus", "good"], seen
    assert all("good" not in key for key in check._taken), "the key itself is not kept, only its digest"


@pytest.mark.asyncio
async def test_the_keys_kept_are_bounded(monkeypatch):
    seen: list[str | None] = []
    client = _orchestrator_at({f"key-{i}": 200 for i in range(4)}, seen)
    monkeypatch.setattr(server, "_client", client)
    check = KeyCheck(max_entries=2, clock=lambda: 0.0)
    try:
        for key in ("key-0", "key-1", "key-0", "key-2", "key-0", "key-1"):
            assert await check(key) is True
    finally:
        await client.aclose()
    assert len(check._taken) == 2
    assert seen == ["key-0", "key-1", "key-2", "key-1"], "key-1, least recently used, went first"


@pytest.mark.asyncio
async def test_client_forwards_the_caller_bearer_over_the_env_key():
    """The orchestrator must see the CALLER's key, never a service-wide one."""
    captured: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers.get("Authorization"))
        return httpx.Response(200, json={})

    client = Embry0Client("http://x", api_key="service-env-key", transport=httpx.MockTransport(handler))
    token = caller_bearer.set("emb_caller-key")
    try:
        await client._request("GET", "/api/v1/queue")
    finally:
        caller_bearer.reset(token)
        await client.aclose()
    assert captured == ["Bearer emb_caller-key"]


@pytest.mark.asyncio
async def test_client_falls_back_to_the_env_key_without_a_caller():
    captured: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers.get("Authorization"))
        return httpx.Response(200, json={})

    client = Embry0Client("http://x", api_key="service-env-key", transport=httpx.MockTransport(handler))
    try:
        await client._request("GET", "/api/v1/queue")
    finally:
        await client.aclose()
    assert captured == ["Bearer service-env-key"]


def test_app_builds_stateless_on_the_mcp_path():
    app = build_app()
    assert isinstance(app, BearerForwardMiddleware)


def test_app_disables_host_validation_unless_allowlisted(monkeypatch):
    """Behind nginx on arbitrary hostnames the SDK's localhost-only Host
    check 421s every request; rebinding doesn't apply to a Bearer-gated
    API. MCP_ALLOWED_HOSTS re-enables it with an explicit list. What each
    policy does to a request is pinned in test_http_wire.py."""
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    assert transport_security().enable_dns_rebinding_protection is False

    monkeypatch.setenv("MCP_ALLOWED_HOSTS", " acme.embry0.ai , ,other.embry0.ai:* ")
    ts = transport_security()
    assert ts.enable_dns_rebinding_protection is True
    assert ts.allowed_hosts == ["acme.embry0.ai", "other.embry0.ai:*"]
    assert ts.allowed_origins == []
