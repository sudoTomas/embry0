"""The hosted MCP endpoint over real HTTP requests: who gets in, and what they see.

These drive the whole ASGI app from ``build_app()`` (bearer gate, SDK
transport, tools) with an in-process HTTP client and a fake orchestrator,
so they hold whatever the MCP SDK does underneath. The outcomes were
recorded against mcp 1.30 before the port to mcp 2 (#503) and must not move:

- ``MCP_ALLOWED_HOSTS`` unset: any Host is served (the service sits behind
  nginx on arbitrary hostnames). Set: only those hosts, anything else 421,
  and any Origin header 403 (no origins are allowlisted).
- No bearer: 401 at the door. A bearer the orchestrator does not take: 401
  at the door too, before ``initialize``, the tool or prompt lists or any
  call (until #599's review it got through to them); one it cannot check
  now: 503.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from embry0.mcp_server import server
from embry0.mcp_server.client import Embry0Client
from embry0.mcp_server.http import build_app

GOOD_KEY = "emb_good-key"
ALLOWED = "mcp.example.test, other.example.test:*"
PROTOCOL = "2025-06-18"


def _orchestrator(request: httpx.Request) -> httpx.Response:
    """A fake orchestrator that, like the real one, refuses any other key."""
    if request.headers.get("authorization") != f"Bearer {GOOD_KEY}":
        return httpx.Response(401, json={"detail": "Invalid or missing API key"})
    if request.url.path == "/api/v1/tools":
        return httpx.Response(200, json=[])
    if request.url.path == "/api/v1/health/ready":
        return httpx.Response(200, json={"status": "ready"})
    if request.url.path == "/api/v1/jobs":
        return httpx.Response(200, json={"jobs": [], "total": 0})
    return httpx.Response(404, json={"detail": f"no route {request.url.path}"})


@asynccontextmanager
async def _endpoint(
    monkeypatch: pytest.MonkeyPatch,
    allowed_hosts: str | None,
    upstream: Callable[[httpx.Request], httpx.Response] = _orchestrator,
) -> AsyncIterator[httpx.AsyncClient]:
    if allowed_hosts is None:
        monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    else:
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", allowed_hosts)
    client = Embry0Client("http://orchestrator.test", api_key="", transport=httpx.MockTransport(upstream))
    monkeypatch.setattr(server, "_client", client)
    app = build_app()
    try:
        async with server.mcp.session_manager.run():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9200") as http:
                yield http
    except BaseExceptionGroup as group:
        # The session manager's task group wraps a failing assertion; report it bare.
        if len(group.exceptions) == 1:
            raise group.exceptions[0] from None
        raise
    finally:
        await client.aclose()
        await _stop_sse_shutdown_watcher()


async def _stop_sse_shutdown_watcher() -> None:
    """sse-starlette starts one shutdown watcher per event loop and only a server
    shutdown ends it; outside uvicorn it outlives the test's loop and is
    reported as a destroyed pending task. Cancelling it lets the next loop
    start its own."""
    watchers = [t for t in asyncio.all_tasks() if getattr(t.get_coro(), "__name__", "") == "_shutdown_watcher"]
    for task in watchers:
        task.cancel()
    await asyncio.gather(*watchers, return_exceptions=True)


async def _rpc(
    http: httpx.AsyncClient, method: str, params: dict[str, Any] | None = None, **headers: str | None
) -> tuple[int, Any]:
    """POST one JSON-RPC request to /mcp; return (status, decoded body)."""
    sent = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "mcp-protocol-version": PROTOCOL,
        "authorization": f"Bearer {GOOD_KEY}",
    }
    for name, value in headers.items():
        name = name.replace("_", "-")
        if value is None:
            sent.pop(name, None)
        else:
            sent[name] = value
    message = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    response = await http.post("/mcp", json=message, headers=sent)
    if "text/event-stream" in response.headers.get("content-type", ""):
        data = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
        return response.status_code, json.loads(data[0])
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, response.text


def _tool_text(body: Any) -> tuple[bool, str]:
    result = body["result"]
    return bool(result.get("isError")), result["content"][0]["text"]


# ---------------------------------------------------------- transport security


async def test_without_an_allowlist_any_host_is_served(monkeypatch):
    async with _endpoint(monkeypatch, allowed_hosts=None) as http:
        for host in ("embry0.example.test", "evil.example", "127.0.0.1:9200"):
            status, body = await _rpc(http, "tools/list", host=host)
            assert status == 200, f"Host {host} got {status}: {body}"
            assert body["result"]["tools"], host
        status, _ = await _rpc(http, "tools/list", host="embry0.example.test", origin="https://elsewhere.example")
        assert status == 200, "an Origin header must not matter without an allowlist"


async def test_an_allowlisted_host_is_served(monkeypatch):
    async with _endpoint(monkeypatch, allowed_hosts=ALLOWED) as http:
        for host in ("mcp.example.test", "other.example.test:8443"):
            status, body = await _rpc(http, "tools/list", host=host)
            assert status == 200, f"allowlisted Host {host} got {status}: {body}"


async def test_a_host_off_the_allowlist_gets_421(monkeypatch):
    async with _endpoint(monkeypatch, allowed_hosts=ALLOWED) as http:
        for host in ("evil.example", "127.0.0.1:9200", "mcp.example.test.evil.example"):
            status, body = await _rpc(http, "tools/list", host=host)
            assert (status, body) == (421, "Invalid Host header"), f"Host {host}"


async def test_with_an_allowlist_any_origin_gets_403(monkeypatch):
    """No origin is allowlisted, so a browser-originated request is refused."""
    async with _endpoint(monkeypatch, allowed_hosts=ALLOWED) as http:
        for origin in ("https://evil.example", "https://mcp.example.test"):
            status, body = await _rpc(http, "tools/list", host="mcp.example.test", origin=origin)
            assert (status, body) == (403, "Invalid Origin header"), f"Origin {origin}"


# ------------------------------------------------------------------------ auth


@pytest.mark.parametrize("authorization", [None, "Bearer", "Basic Zm9vOmJhcg==", f"Token {GOOD_KEY}"])
async def test_a_request_without_a_bearer_is_refused_at_the_door(monkeypatch, authorization):
    async with _endpoint(monkeypatch, allowed_hosts=None) as http:
        status, body = await _rpc(http, "tools/list", authorization=authorization)
    assert status == 401
    assert body == {"error": "unauthorized", "detail": "Authorization: Bearer <embry0 API key> is required"}


async def test_the_door_runs_before_the_host_check(monkeypatch):
    async with _endpoint(monkeypatch, allowed_hosts=ALLOWED) as http:
        status, _ = await _rpc(http, "tools/list", host="evil.example", authorization=None)
    assert status == 401


DISCOVERY = [
    ("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}),
    ("tools/list", {}),
    ("prompts/list", {}),
    ("prompts/get", {"name": "setup"}),
    ("tools/call", {"name": "job_list", "arguments": {}}),
]


@pytest.mark.parametrize(("method", "params"), DISCOVERY)
async def test_a_key_the_orchestrator_does_not_take_is_served_nothing(monkeypatch, method, params):
    async with _endpoint(monkeypatch, allowed_hosts=None) as http:
        status, body = await _rpc(http, method, params, authorization="Bearer emb_wrong-key")
    assert (status, body) == (401, {"error": "unauthorized", "detail": "the embry0 API does not take this key"}), (
        f"{method} answered a key the orchestrator refuses: {status} {body}"
    )


@pytest.mark.parametrize(("method", "params"), DISCOVERY[:3])
async def test_a_key_the_orchestrator_takes_is_served_discovery(monkeypatch, method, params):
    async with _endpoint(monkeypatch, allowed_hosts=None) as http:
        status, body = await _rpc(http, method, params)
    assert status == 200 and "result" in body, body
    if method == "tools/list":
        assert len(body["result"]["tools"]) > 50, "the key sees the tool surface"


@pytest.mark.parametrize("answer", [500, "unreachable"])
async def test_a_key_the_orchestrator_cannot_check_gets_503(monkeypatch, answer):
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/tools":
            if answer == "unreachable":
                raise httpx.ConnectError("refused")
            return httpx.Response(answer, json={"detail": "boom"})
        return _orchestrator(request)

    async with _endpoint(monkeypatch, allowed_hosts=None, upstream=upstream) as http:
        status, body = await _rpc(http, "tools/list")
    assert (status, body) == (
        503,
        {"error": "unavailable", "detail": "the embry0 API could not check this key; try again"},
    )


async def test_the_callers_key_is_what_the_orchestrator_sees(monkeypatch):
    seen: list[str | None] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return _orchestrator(request)

    async with _endpoint(monkeypatch, allowed_hosts=None, upstream=upstream) as http:
        status, body = await _rpc(http, "tools/call", {"name": "health", "arguments": {}})
    assert status == 200
    assert _tool_text(body)[0] is False, body
    assert body["result"]["structuredContent"] == {"status": "ready"}
    assert seen == [f"Bearer {GOOD_KEY}", f"Bearer {GOOD_KEY}"], "the key check, then the tool's own call"


# --------------------------------------------------------------- what is shown


async def test_an_api_error_reaches_the_caller_with_its_detail(monkeypatch):
    async with _endpoint(monkeypatch, allowed_hosts=None) as http:
        _, body = await _rpc(http, "tools/call", {"name": "job_get", "arguments": {"job_id": "nope"}})
    assert _tool_text(body) == (True, "Error executing tool job_get: embry0 API error 404: no route /api/v1/jobs/nope")


async def test_initialize_carries_the_server_instructions(monkeypatch):
    params = {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}
    async with _endpoint(monkeypatch, allowed_hosts=None) as http:
        status, body = await _rpc(http, "initialize", params)
    assert status == 200
    result = body["result"]
    assert result["serverInfo"]["name"] == "embry0"
    assert result["serverInfo"]["version"], "the server must report a version"
    assert result["instructions"] == server.mcp.instructions
    assert "case_list(awaiting_decision=true)" in result["instructions"]
