"""The xai- and llm-proxy meter every model call they forward, under the run the orchestrator declared for
the sandbox, and forward no billed call the meter cannot read.

The tests reach the meter through the admin API only (``/admin/runs``, ``/admin/meter``), as the orchestrator
does; the answers are the recorded and published ones in ``tests/fixtures/model-answers/``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiohttp
import httpx
import pytest
import structlog.testing
from aiohttp import web
from aiohttp.test_utils import TestClient

from embry0.execution.proxy.llm_proxy import create_llm_proxy_app
from embry0.execution.proxy.xai_proxy import create_xai_proxy_app
from tests.fixtures.model_servers import allow_loopback_test_servers
from tests.fixtures.recording_upstream import is_test_request
from tests.unit.execution.proxy.upstream_stub import route_upstream

ANSWERS = Path(__file__).parents[3] / "fixtures" / "model-answers"
_ADMIN = "test-admin-secret-not-real"
_TOKEN = "sandbox-bearer-token-at-least-32-chars-long"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}
_ADMIN_HEADERS = {"X-Admin-Token": _ADMIN}
_SSE = "text/event-stream"

#: The limits every enrollment carries (the settings document's local_models; the section's defaults here).
LIMITS: dict[str, Any] = {"max_request_bytes": 8 * 1024 * 1024, "timeout_seconds": 900.0}


class _Answer:
    """One upstream answer: its status, content type and the chunks it streams, or how it fails."""

    def __init__(
        self,
        body: bytes = b"",
        *,
        status: int = 200,
        content_type: str = "application/json",
        chunk: int = 50,
        fail: BaseException | None = None,
        fail_after: int | None = None,
        gate: asyncio.Event | None = None,
        encoding: str | None = None,
    ) -> None:
        self.status_code = status
        self.headers = {"content-type": content_type}
        if encoding is not None:
            self.headers["content-encoding"] = encoding
        self._chunks = [body[i : i + chunk] for i in range(0, len(body), chunk)] or [b""]
        self._fail = fail
        self._fail_after = fail_after
        self._gate = gate

    async def __aenter__(self) -> _Answer:
        if self._fail is not None and self._fail_after is None:
            raise self._fail
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        for i, piece in enumerate(self._chunks):
            if self._fail is not None and self._fail_after == i:
                raise self._fail
            if self._gate is not None and i == 1:
                await self._gate.wait()
            yield piece
        if self._fail is not None and self._fail_after == len(self._chunks):
            raise self._fail  # after the whole answer


class _Upstream:
    """Stands in for the proxy's httpx client: answers each call with the next queued answer."""

    def __init__(self) -> None:
        self.answers: list[_Answer] = []
        self.calls: list[dict[str, Any]] = []

    def stream(self, method: str = "", url: str = "", **kw: Any) -> _Answer:
        self.calls.append({"method": kw.get("method", method), "url": kw.get("url", url), **kw})
        return self.answers.pop(0)


async def _xai(aiohttp_client: Any, upstream: _Upstream, **kw: Any) -> TestClient:
    client = await aiohttp_client(create_xai_proxy_app(_ADMIN, **kw))
    enrolled = await client.post(
        "/admin/enroll", json={"sandbox_id": "sbx-1", "sandbox_token": _TOKEN}, headers=_ADMIN_HEADERS
    )
    assert enrolled.status == 200
    assert (await client.post("/admin/token", json={"access_token": "at-1"}, headers=_ADMIN_HEADERS)).status == 200
    route_upstream(client.app, upstream)
    return client


async def _declare(client: TestClient, run_id: str = "run-1", sandbox_id: str = "sbx-1") -> Any:
    return await client.post(
        "/admin/runs", json={"sandbox_id": sandbox_id, "run_id": run_id, "job_id": "job-1"}, headers=_ADMIN_HEADERS
    )


async def _meter(client: TestClient) -> dict[str, Any]:
    resp = await client.get("/admin/meter", headers=_ADMIN_HEADERS)
    assert resp.status == 200, f"no meter to read (HTTP {resp.status})"
    return await resp.json()


async def _run(client: TestClient, run_id: str = "run-1") -> dict[str, Any]:
    tallies = [t for t in (await _meter(client))["tallies"] if t["run_id"] == run_id]
    assert len(tallies) == 1, tallies
    return tallies[0]


def _messages(**body: Any) -> dict[str, Any]:
    return {"model": "grok-4.7", "max_tokens": 32000, "stream": True, "messages": [], **body}


# --------------------------------------------------------------------------- xai-proxy


async def test_a_declared_run_is_charged_its_streamed_answer_and_the_answer_passes_unchanged(aiohttp_client):
    upstream = _Upstream()
    raw = (ANSWERS / "anthropic-stream.sse").read_bytes()
    upstream.answers.append(_Answer(raw, content_type=_SSE, chunk=13))
    client = await _xai(aiohttp_client, upstream)
    assert (await _declare(client)).status == 200, "the proxy took no run declaration"
    resp = await client.post("/v1/messages", json=_messages(), headers=_AUTH)
    assert resp.status == 200
    assert await resp.read() == raw, "the answer was changed on its way through"
    model = (await _run(client))["models"]["grok-4.7"]
    assert (model["input_tokens"], model["output_tokens"], model["cache_read_tokens"]) == (1000, 100, 400)
    assert (model["requests"], model["estimated"]) == (1, 0)
    ended = await client.delete("/admin/runs/run-1", headers=_ADMIN_HEADERS)
    assert ended.status == 200
    assert (await ended.json())["tally"]["models"]["grok-4.7"]["output_tokens"] == 100
    assert (await client.delete("/admin/runs/run-1", headers=_ADMIN_HEADERS)).status == 404


async def test_a_billed_call_the_meter_cannot_read_is_refused_before_it_goes_upstream(aiohttp_client):
    upstream = _Upstream()
    client = await _xai(aiohttp_client, upstream)
    for path in ("/v1/responses", "/v1/images/generations", "/v1/messages/batches"):
        resp = await client.post(path, json={"model": "grok-4.7"}, headers=_AUTH)
        assert resp.status == 404, f"{path} went upstream unmetered"
    assert upstream.calls == []


async def test_reads_and_token_counts_pass_and_cost_nothing(aiohttp_client):
    upstream = _Upstream()
    upstream.answers += [_Answer(b'{"data": []}'), _Answer(b'{"input_tokens": 10}')]
    client = await _xai(aiohttp_client, upstream)
    await _declare(client)
    assert (await client.get("/v1/models", headers=_AUTH)).status == 200
    assert (await client.post("/v1/messages/count_tokens", json={"model": "grok-4.7"}, headers=_AUTH)).status == 200
    tally = await _run(client)
    assert (tally["models"], tally["errors"]) == ({}, 0)


async def test_a_streaming_chat_call_goes_upstream_asking_for_its_usage(aiohttp_client):
    upstream = _Upstream()
    upstream.answers.append(_Answer((ANSWERS / "openai-chat-stream.sse").read_bytes(), content_type=_SSE))
    client = await _xai(aiohttp_client, upstream)
    await _declare(client)
    resp = await client.post(
        "/v1/chat/completions", json={"model": "grok-4.7", "stream": True, "messages": []}, headers=_AUTH
    )
    assert resp.status == 200
    sent = json.loads(upstream.calls[0]["content"])
    assert sent["stream_options"] == {"include_usage": True}, "the stream would carry no usage"
    assert "Content-Length" not in upstream.calls[0]["headers"] and "content-length" not in upstream.calls[0]["headers"]
    model = (await _run(client))["models"]["gpt-5"]
    assert (model["input_tokens"], model["output_tokens"], model["cache_read_tokens"]) == (1376, 64, 1024)


@pytest.mark.parametrize(
    "timeout", [httpx.ReadTimeout("slow"), httpx.ConnectTimeout("no connection yet")], ids=["read", "connect"]
)
async def test_an_answer_that_times_out_is_charged_its_ceiling(aiohttp_client, timeout):
    """A connect timeout too: the proxy cannot tell whether the request left, so it counts the most it could cost."""
    upstream = _Upstream()
    upstream.answers.append(_Answer(fail=timeout))
    client = await _xai(aiohttp_client, upstream)
    await _declare(client)
    body = _messages(max_tokens=4000)
    resp = await client.post("/v1/messages", json=body, headers=_AUTH)
    assert resp.status == 504
    model = (await _run(client))["models"]["grok-4.7"]
    assert (model["output_tokens"], model["estimated"]) == (4000, 1), "a lost answer cost less than its ceiling"
    assert model["input_tokens"] >= len(json.dumps(body)) // 2


async def test_an_upstream_that_fails_after_the_request_went_is_charged_the_ceiling(aiohttp_client):
    """Any failure past the connect, before an answer: the provider may have run the call, so it costs its ceiling."""
    upstream = _Upstream()
    upstream.answers.append(_Answer(fail=httpx.RemoteProtocolError("server disconnected")))
    client = await _xai(aiohttp_client, upstream)
    await _declare(client)
    resp = await client.post("/v1/messages", json=_messages(max_tokens=900), headers=_AUTH)
    assert resp.status == 500
    model = (await _run(client))["models"]["grok-4.7"]
    assert (model["output_tokens"], model["estimated"]) == (900, 1)


async def test_a_stream_cut_midway_keeps_its_input_and_is_charged_its_output_ceiling(aiohttp_client):
    """The provider may have generated the whole answer: what was read of it is no measure of its output."""
    upstream = _Upstream()
    raw = (ANSWERS / "anthropic-stream.sse").read_bytes()
    upstream.answers.append(
        _Answer(raw, content_type=_SSE, chunk=400, fail=httpx.RemoteProtocolError("cut"), fail_after=2)
    )
    client = await _xai(aiohttp_client, upstream)
    await _declare(client)
    resp = await client.post("/v1/messages", json=_messages(), headers=_AUTH)
    await resp.read()
    model = (await _run(client))["models"]["grok-4.7"]
    assert (model["input_tokens"], model["cache_read_tokens"], model["estimated"]) == (1000, 400, 1)
    assert model["output_tokens"] == 32000, f"a cut stream was charged an estimate, not its ceiling: {model}"


async def test_a_call_that_never_connected_and_an_error_answer_cost_nothing(aiohttp_client):
    upstream = _Upstream()
    upstream.answers += [
        _Answer(fail=httpx.ConnectError("down")),
        _Answer(b'{"type": "error", "error": {"type": "overloaded_error"}}', status=529),
    ]
    client = await _xai(aiohttp_client, upstream)
    await _declare(client)
    assert (await client.post("/v1/messages", json=_messages(), headers=_AUTH)).status == 502
    assert (await client.post("/v1/messages", json=_messages(), headers=_AUTH)).status == 529
    tally = await _run(client)
    assert (tally["models"], tally["errors"], tally["open"]) == ({}, 1, [])


async def test_a_client_that_goes_away_midstream_is_charged_its_ceiling(aiohttp_client):
    """A sandbox that hangs up mid-answer does not pay for only what it read."""
    upstream = _Upstream()
    gate = asyncio.Event()
    raw = (ANSWERS / "anthropic-stream.sse").read_bytes()
    upstream.answers.append(_Answer(raw, content_type=_SSE, chunk=300, gate=gate))
    client = await _xai(aiohttp_client, upstream)
    await _declare(client)
    resp = await client.post("/v1/messages", json=_messages(), headers=_AUTH)
    await resp.content.read(100)
    resp.close()
    gate.set()
    for _ in range(100):
        tally = await _run(client)
        if not tally["open"]:
            break
        await asyncio.sleep(0.02)
    model = tally["models"]["grok-4.7"]
    assert (model["requests"], model["estimated"], model["input_tokens"]) == (1, 1, 1000), tally
    assert model["output_tokens"] == 32000, f"a sandbox that went away paid an estimate of what it read: {model}"


async def test_with_a_run_required_a_call_under_none_is_refused_before_it_goes_upstream(aiohttp_client):
    upstream = _Upstream()
    client = await _xai(aiohttp_client, upstream, require_run=True)
    resp = await client.post("/v1/messages", json=_messages(), headers=_AUTH)
    assert resp.status == 403
    assert (await resp.json())["error"]["type"] == "permission_error"
    assert upstream.calls == []


async def test_without_a_run_required_a_call_under_none_still_counts(aiohttp_client):
    upstream = _Upstream()
    upstream.answers.append(_Answer((ANSWERS / "anthropic-message.json").read_bytes()))
    client = await _xai(aiohttp_client, upstream)
    assert (await client.post("/v1/messages", json=_messages(stream=False), headers=_AUTH)).status == 200
    (tally,) = (await _meter(client))["tallies"]
    assert (tally["run_id"], tally["sandbox_id"], tally["attributed"]) == (None, "sbx-1", False)
    assert tally["models"]["grok-4.7"]["output_tokens"] == 300


async def test_the_meter_s_routes_are_the_orchestrator_s(aiohttp_client):
    client = await _xai(aiohttp_client, _Upstream())
    run = {"sandbox_id": "sbx-1", "run_id": "run-1"}
    assert (await client.post("/admin/runs", json=run)).status == 401
    assert (await client.get("/admin/meter")).status == 401
    assert (await client.delete("/admin/runs/run-1")).status == 401
    assert (await _declare(client, sandbox_id="sbx-unknown")).status == 404
    assert (await _declare(client, run_id="run/../1")).status == 400
    bad = await client.post("/admin/runs", json={"sandbox_id": "sbx-1", "run_id": 7}, headers=_ADMIN_HEADERS)
    assert bad.status == 400


async def test_an_enrollment_that_ends_ends_its_run_which_stays_readable(aiohttp_client):
    upstream = _Upstream()
    upstream.answers.append(_Answer((ANSWERS / "anthropic-message.json").read_bytes()))
    client = await _xai(aiohttp_client, upstream)
    await _declare(client)
    await client.post("/v1/messages", json=_messages(stream=False), headers=_AUTH)
    assert (await client.delete("/admin/enroll/sbx-1", headers=_ADMIN_HEADERS)).status == 200
    tally = await _run(client)
    assert tally["ended"] is not None
    assert tally["models"]["grok-4.7"]["output_tokens"] == 300


# --------------------------------------------------------------------------- llm-proxy


@pytest.fixture
async def model_server(aiohttp_server) -> Any:
    """A fake model server answering each chat completion with the next queued (content type, body)."""
    seen: list[dict[str, Any]] = []
    answers: list[tuple[str, bytes]] = []

    async def record(request: web.Request) -> web.StreamResponse:
        if not is_test_request(request.path, request.headers, paths=["/v1/"], credentials=[]):
            return web.Response(status=404)
        seen.append({"path": request.path, "body": await request.read()})
        if request.path.endswith("/models"):
            return web.json_response({"data": [{"id": "qwen"}]})
        content_type, body = answers.pop(0)
        return web.Response(body=body, content_type=content_type)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", record)
    server = await aiohttp_server(app)
    server.seen = seen
    server.answers = answers
    return server


@pytest.fixture(autouse=True)
def _fake_servers_on_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake model servers here listen on 127.0.0.1, which no real server may be (ADR 0032)."""
    allow_loopback_test_servers(monkeypatch)


async def _local(aiohttp_client: Any, server: Any, *, providers: bool = False, **kw: Any) -> TestClient:
    """The llm-proxy with sbx-1 enrolled with ``server`` for the custom provider, and for openai too when ``providers``."""
    client = await aiohttp_client(create_llm_proxy_app(_ADMIN, **kw))
    server_of_sandbox = {
        "credential_id": "cred-a",
        "upstream": f"http://127.0.0.1:{server.port}/v1",
        "api_key": "",
        "address": "127.0.0.1",
    }
    body: dict[str, Any] = {"sandbox_id": "sbx-1", "sandbox_token": _TOKEN, "local_llm": server_of_sandbox}
    if providers:
        body["provider_servers"] = {"openai": {**server_of_sandbox, "credential_id": "cred-o"}}
    enrolled = await client.post("/admin/enroll", json={**body, "limits": LIMITS}, headers=_ADMIN_HEADERS)
    assert enrolled.status == 200
    return client


async def test_a_local_model_s_chat_completions_are_charged_to_the_run(aiohttp_client, model_server):
    model_server.answers.append(("application/json", (ANSWERS / "openai-chat.json").read_bytes()))
    model_server.answers.append(("text/event-stream", (ANSWERS / "openai-chat-stream.sse").read_bytes()))
    client = await _local(aiohttp_client, model_server)
    assert (await _declare(client)).status == 200, "the proxy took no run declaration"
    assert (await client.get("/v1/models", headers=_AUTH)).status == 200
    chat = {"model": "qwen", "messages": []}
    assert (await client.post("/v1/chat/completions", json=chat, headers=_AUTH)).status == 200
    streamed = await client.post("/v1/chat/completions", json={**chat, "stream": True}, headers=_AUTH)
    assert streamed.status == 200
    await streamed.read()
    assert json.loads(model_server.seen[-1]["body"])["stream_options"] == {"include_usage": True}
    models = (await _run(client))["models"]
    assert (models["deepseek-upstream-id"]["input_tokens"], models["deepseek-upstream-id"]["output_tokens"]) == (
        1000,
        80,
    )
    assert models["gpt-5"]["output_tokens"] == 64


async def test_a_local_model_call_under_no_run_is_refused_when_one_is_required(aiohttp_client, model_server):
    client = await _local(aiohttp_client, model_server, require_run=True)
    resp = await client.post("/v1/chat/completions", json={"model": "qwen", "messages": []}, headers=_AUTH)
    assert resp.status == 403
    assert (await resp.json())["error"]["code"] == "no_run"
    assert model_server.seen == []


@pytest.mark.parametrize(
    ("failure", "status"),
    [(httpx.ReadTimeout("slow"), 504), (httpx.RemoteProtocolError("server disconnected"), 500)],
    ids=["a timeout", "a failure past the connect"],
)
async def test_a_local_model_call_whose_answer_is_lost_is_charged_its_ceiling(
    aiohttp_client, model_server, failure, status
):
    client = await _local(aiohttp_client, model_server)
    upstream = _Upstream()
    upstream.answers.append(_Answer(fail=failure))
    route_upstream(client.app, upstream)
    await _declare(client)
    chat = {"model": "qwen", "messages": [], "max_tokens": 600}
    assert (await client.post("/v1/chat/completions", json=chat, headers=_AUTH)).status == status
    model = (await _run(client))["models"]["qwen"]
    assert (model["output_tokens"], model["estimated"]) == (600, 1)


# --------------------------------------------------------------------------- an answer that breaks off


#: Each metering proxy's route, the answer its upstream streams, and the output ceiling of the call made.
_ROUTES = {
    "xai-proxy": ("/v1/messages", "anthropic-stream.sse", 900),
    "llm-proxy /v1": ("/v1/chat/completions", "openai-chat-stream.sse", 700),
    "llm-proxy /p/openai": ("/p/openai/v1/chat/completions", "openai-chat-stream.sse", 700),
}


async def _metered(aiohttp_client: Any, model_server: Any, route: str, upstream: _Upstream) -> TestClient:
    """A metering proxy with sbx-1 enrolled and a run declared, its upstream calls answered by ``upstream``."""
    if route == "xai-proxy":
        client = await _xai(aiohttp_client, upstream)
    else:
        client = await _local(aiohttp_client, model_server, providers=True)
        route_upstream(client.app, upstream)
    assert (await _declare(client)).status == 200
    return client


def _call(route: str) -> dict[str, Any]:
    ceiling = _ROUTES[route][2]
    if route == "xai-proxy":
        return _messages(max_tokens=ceiling)
    return {"model": "qwen", "messages": [], "stream": True, "max_tokens": ceiling}


async def _settled(client: TestClient) -> dict[str, Any]:
    """The run's tally once no call is in flight."""
    for _ in range(200):
        tally = await _run(client)
        if not tally["open"]:
            return tally
        await asyncio.sleep(0.01)
    raise AssertionError(f"a call stayed in flight: {tally}")


@pytest.mark.parametrize("route", sorted(_ROUTES))
@pytest.mark.parametrize("cause", ["the upstream cut it", "it could not be decoded", "the sandbox went away"])
async def test_an_answer_broken_off_before_its_usage_is_charged_its_ceiling_once(
    aiohttp_client, model_server, route, cause
):
    path, answer, ceiling = _ROUTES[route]
    raw = (ANSWERS / answer).read_bytes()
    upstream = _Upstream()
    gate = asyncio.Event()
    if cause == "the upstream cut it":
        upstream.answers.append(_Answer(raw, content_type=_SSE, chunk=300, fail=httpx.ReadError("cut"), fail_after=1))
    elif cause == "it could not be decoded":
        upstream.answers.append(
            _Answer(raw, content_type=_SSE, chunk=300, fail=httpx.DecodingError("bad gzip data"), fail_after=1)
        )
    else:
        upstream.answers.append(_Answer(raw, content_type=_SSE, chunk=300, gate=gate))
    client = await _metered(aiohttp_client, model_server, route, upstream)

    with structlog.testing.capture_logs() as logs:
        resp = await client.post(path, json=_call(route), headers=_AUTH)
        if cause == "the sandbox went away":
            await resp.content.read(100)
            resp.close()
            gate.set()
        else:
            try:
                await resp.read()
            except aiohttp.ClientPayloadError:
                pass  # the answer comes cut short, which is the point
        tally = await _settled(client)
    if cause != "the sandbox went away":
        truncated = f"{route.split()[0].replace('-', '_')}_stream_truncated"
        assert [e["event"] for e in logs if e["event"] == truncated], f"{cause}, nothing said so: {logs}"

    assert len(tally["models"]) == 1, tally
    (counted,) = tally["models"].values()
    assert counted["output_tokens"] == ceiling, f"{cause}, the call was charged {counted['output_tokens']}: {tally}"
    assert (counted["requests"], counted["estimated"], tally["errors"]) == (1, 1, 0), f"counted twice or not: {tally}"


@pytest.mark.parametrize("route", sorted(_ROUTES))
async def test_an_answer_that_breaks_after_its_usage_is_charged_that_usage(aiohttp_client, model_server, route):
    """Its usage was read in full: that is what it cost, however it ended."""
    path, answer, _ceiling = _ROUTES[route]
    raw = (ANSWERS / answer).read_bytes()
    upstream = _Upstream()
    whole = -(-len(raw) // 300)  # its chunks: it breaks once every one of them was read
    upstream.answers.append(_Answer(raw, content_type=_SSE, chunk=300, fail=httpx.ReadError("cut"), fail_after=whole))
    client = await _metered(aiohttp_client, model_server, route, upstream)
    await (await client.post(path, json=_call(route), headers=_AUTH)).read()
    (counted,) = (await _settled(client))["models"].values()
    expected = 100 if route == "xai-proxy" else 64
    assert (counted["output_tokens"], counted["estimated"]) == (expected, 0), counted


@pytest.mark.parametrize("route", sorted(_ROUTES))
async def test_an_error_answer_that_breaks_off_still_costs_nothing(aiohttp_client, model_server, route):
    path, _answer, _ceiling = _ROUTES[route]
    upstream = _Upstream()
    error = b'{"error": {"type": "overloaded_error", "message": "' + b"x" * 400 + b'"}}'
    upstream.answers.append(_Answer(error, status=529, chunk=100, fail=httpx.ReadError("cut"), fail_after=2))
    client = await _metered(aiohttp_client, model_server, route, upstream)
    await (await client.post(path, json=_call(route), headers=_AUTH)).read()
    tally = await _settled(client)
    assert (tally["models"], tally["errors"]) == ({}, 1), tally


async def test_a_gzip_answer_that_turns_to_garbage_is_charged_its_ceiling(aiohttp_client, aiohttp_server):
    """A real server, through the proxy's own client: a 200 whose gzip body stops decoding halfway."""
    import zlib

    raw = (ANSWERS / "openai-chat-stream.sse").read_bytes()

    async def answer(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(status=200, headers={"Content-Type": _SSE, "Content-Encoding": "gzip"})
        await response.prepare(request)
        packer = zlib.compressobj(wbits=31)
        await response.write(packer.compress(raw[:300]) + packer.flush(zlib.Z_SYNC_FLUSH))  # decodes
        await response.write(b"\xff" * 64 + b"not gzip at all")  # does not
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/v1/chat/completions", answer)
    server = await aiohttp_server(app)
    client = await _local(aiohttp_client, server)
    assert (await _declare(client)).status == 200
    resp = await client.post("/v1/chat/completions", json=_call("llm-proxy /v1"), headers=_AUTH)
    try:
        await resp.read()
    except aiohttp.ClientPayloadError:
        pass
    (counted,) = (await _settled(client))["models"].values()
    assert (counted["requests"], counted["output_tokens"]) == (1, 700), f"charged {counted}"


@pytest.mark.parametrize("route", sorted(_ROUTES))
async def test_an_answer_that_ends_whole_without_its_usage_is_charged_an_estimate(aiohttp_client, model_server, route):
    """Not broken off, only unreadable (a shape this release does not know): it carried all it generated, so an
    estimate from its bytes stands."""
    path, _answer, ceiling = _ROUTES[route]
    body = json.dumps({"id": "unknown-shape", "said": "x" * 200}).encode()
    upstream = _Upstream()
    upstream.answers.append(_Answer(body, chunk=40))
    client = await _metered(aiohttp_client, model_server, route, upstream)
    await (await client.post(path, json=_call(route), headers=_AUTH)).read()
    (counted,) = (await _settled(client))["models"].values()
    assert (counted["estimated"], counted["output_tokens"]) == (1, -(-len(body) // 2)), (
        f"a whole answer was charged {counted['output_tokens']}, not an estimate of its bytes (ceiling {ceiling})"
    )


def _sse(*events: dict[str, Any]) -> bytes:
    return b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)


async def test_a_broken_stream_s_ceiling_raises_its_output_and_leaves_its_input_counts(aiohttp_client):
    """A large request whose stream broke after its input counts: those counts stand (its input is not raised to the
    request's size besides them), and its output is the most the request allowed."""
    start = {
        "type": "message_start",
        "message": {
            "id": "m",
            "model": "grok-4.7",
            "usage": {
                "input_tokens": 10,
                "cache_read_input_tokens": 100_000,
                "cache_creation_input_tokens": 2000,
                "output_tokens": 1,
            },
        },
    }
    delta = {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Reading"}}
    raw = _sse(start, delta)
    upstream = _Upstream()
    upstream.answers.append(
        _Answer(raw, content_type=_SSE, chunk=len(raw) // 2 + 1, fail=httpx.ReadError("cut"), fail_after=2)
    )
    client = await _metered(aiohttp_client, None, "xai-proxy", upstream)
    body = _messages(max_tokens=900, messages=[{"role": "user", "content": "x" * 400_000}])
    await (await client.post("/v1/messages", json=body, headers=_AUTH)).read()
    (counted,) = (await _settled(client))["models"].values()
    said = (counted["input_tokens"], counted["cache_read_tokens"], counted["cache_creation_tokens"])
    assert said == (10, 100_000, 2000), f"the input counts the answer gave were not what it was charged: {counted}"
    assert counted["output_tokens"] == 900


@pytest.mark.parametrize("route", ["llm-proxy /v1", "llm-proxy /p/openai"])
async def test_a_running_usage_in_each_chunk_does_not_make_a_broken_stream_whole(aiohttp_client, model_server, route):
    """A server that sends a running usage with every chunk (vLLM's continuous_usage_stats) has not said what the answer
    cost until a chunk finishes it."""
    path, _answer, ceiling = _ROUTES[route]

    def chunk(text: str, done: int) -> dict[str, Any]:
        usage = {"prompt_tokens": 20, "completion_tokens": done, "total_tokens": 20 + done}
        choice = {"index": 0, "delta": {"content": text}, "finish_reason": None}
        return {"id": "c", "model": "qwen", "choices": [choice], "usage": usage}

    raw = _sse(chunk("Read", 1), chunk("ing the file", 4), chunk(" now", 5))
    upstream = _Upstream()
    first = raw.index(b"\n\n") + 2
    upstream.answers.append(_Answer(raw, content_type=_SSE, chunk=first, fail=httpx.ReadError("cut"), fail_after=1))
    client = await _metered(aiohttp_client, model_server, route, upstream)
    await (await client.post(path, json=_call(route), headers=_AUTH)).read()
    (counted,) = (await _settled(client))["models"].values()
    assert counted["output_tokens"] == ceiling, f"a running usage was taken for the answer's: {counted}"


@pytest.mark.parametrize("route", ["llm-proxy /v1", "llm-proxy /p/openai"])
async def test_an_early_usage_alone_leaves_a_whole_stream_estimated(aiohttp_client, model_server, route):
    """A usage in a chunk whose choice is still open is not the answer's: one that never finishes with another is
    charged an estimate of what it carried, not that early usage's output."""
    path, _answer, _ceiling = _ROUTES[route]
    early = {
        "id": "c",
        "model": "qwen",
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 0, "total_tokens": 20},
    }
    text = {
        "id": "c",
        "model": "qwen",
        "choices": [{"index": 0, "delta": {"content": "y" * 300}, "finish_reason": None}],
    }
    raw = _sse(early, text) + b"data: [DONE]\n\n"
    upstream = _Upstream()
    upstream.answers.append(_Answer(raw, content_type=_SSE, chunk=200))
    client = await _metered(aiohttp_client, model_server, route, upstream)
    await (await client.post(path, json=_call(route), headers=_AUTH)).read()
    (counted,) = (await _settled(client))["models"].values()
    assert (counted["estimated"], counted["output_tokens"]) == (1, 150), f"charged {counted}"


async def test_the_xai_proxy_asks_for_plain_answers_and_refuses_one_it_cannot_read(aiohttp_client):
    """An answer in an encoding the proxy does not decode would reach the sandbox unread by the meter."""
    upstream = _Upstream()
    raw = (ANSWERS / "anthropic-stream.sse").read_bytes()
    upstream.answers.append(_Answer(raw, content_type=_SSE, encoding="br"))
    client = await _metered(aiohttp_client, None, "xai-proxy", upstream)
    headers = {**_AUTH, "Accept-Encoding": "br, zstd"}
    resp = await client.post("/v1/messages", json=_messages(max_tokens=900), headers=headers)
    body = await resp.read()
    assert resp.status == 502, f"an answer in br went back: {resp.status} {body[:120]!r}"
    sent = {name.lower(): value for name, value in upstream.calls[0]["headers"].items()}
    assert sent.get("accept-encoding") == "identity", f"the upstream was asked for: {sent.get('accept-encoding')}"
    (counted,) = (await _settled(client))["models"].values()
    assert (counted["requests"], counted["output_tokens"]) == (1, 900), f"a refused completion was charged {counted}"


#: Each metered completion route, and the wire its body is read in.
_COMPLETION_ROUTES = {
    "xai-proxy messages": ("xai-proxy", "/v1/messages"),
    "xai-proxy chat": ("xai-proxy", "/v1/chat/completions"),
    "llm-proxy /v1": ("llm-proxy /v1", "/v1/chat/completions"),
    "llm-proxy /p/openai": ("llm-proxy /p/openai", "/p/openai/v1/chat/completions"),
}


@pytest.mark.parametrize("route", sorted(_COMPLETION_ROUTES))
@pytest.mark.parametrize(
    "raw",
    [
        b'{"model": "m", "max_tokens": 10, "MAX_TOKENS": 100000}',
        '{"model": "m", "max_tokens": 10, "max_tokenſ": 100000}'.encode(),
        b'{"model": "m", "max_tokens": 10} trailing',
        b'{"model": "m\xff", "max_tokens": 10}',
        b'{"model": "m", "max_tokens": 10, "stream": "false"}',
        b'{"model": "m", "max_tokens": "1000000"}',
        b'{"model": "m", "max_tokens": 1e6}',
        b'{"model": "m", "max_tokens": 1000000000000000000}',
        b'{"model": "m", "max_tokens": 2000000, "n": 6}',
        b'{"model": "m", "max_tokens": 10, "n": 0}',
        b'{"model": "m", "max_tokens": 10, "n_cmpl": 1.5}',
    ],
    ids=[
        "case-folded-twin",
        "long-s-twin",
        "trailing-garbage",
        "not-utf-8",
        "stream-as-text",
        "limit-as-text",
        "limit-1e6",
        "ceiling-1e18",
        "limit-times-answers-past-the-most",
        "no-answers",
        "answers-as-fraction",
    ],
)
async def test_a_completion_body_the_meter_cannot_read_as_every_server_would_is_refused(
    aiohttp_client, model_server, route, raw
):
    proxy, path = _COMPLETION_ROUTES[route]
    upstream = _Upstream()
    client = await _metered(aiohttp_client, model_server, proxy, upstream)
    resp = await client.post(path, data=raw, headers={**_AUTH, "Content-Type": "application/json"})
    assert resp.status == 400, f"{route} answered {resp.status} to {raw!r}"
    assert upstream.calls == [], f"{route} forwarded {raw!r}"
    assert (await _run(client))["models"] == {}, "a refused request was counted"


@pytest.mark.parametrize("route", ["xai-proxy chat", "llm-proxy /v1", "llm-proxy /p/openai"])
async def test_a_completion_naming_both_limits_is_charged_the_larger(aiohttp_client, model_server, route):
    """{"max_completion_tokens": 10, "max_tokens": 100000}: a server that knows only max_tokens generates up to it."""
    proxy, path = _COMPLETION_ROUTES[route]
    raw = (ANSWERS / "openai-chat-stream.sse").read_bytes()
    upstream = _Upstream()
    upstream.answers.append(_Answer(raw, content_type=_SSE, chunk=300, fail=httpx.ReadError("cut"), fail_after=1))
    client = await _metered(aiohttp_client, model_server, proxy, upstream)
    body = {"model": "qwen", "messages": [], "stream": True, "max_completion_tokens": 10, "max_tokens": 100_000}
    try:
        await (await client.post(path, json=body, headers=_AUTH)).read()
    except aiohttp.ClientPayloadError:
        pass
    (counted,) = (await _settled(client))["models"].values()
    assert counted["output_tokens"] == 100_000, f"{route} read the lower limit: {counted}"


@pytest.mark.parametrize("route", ["xai-proxy chat", "llm-proxy /v1", "llm-proxy /p/openai"])
@pytest.mark.parametrize(("extra", "answers"), [({"n": 8}, 8), ({"n": 2, "n_cmpl": 3}, 3)], ids=["n", "n_cmpl"])
async def test_a_completion_asking_for_several_answers_is_charged_its_limit_times_them_after_a_hang_up(
    aiohttp_client, model_server, route, extra, answers
):
    """{"max_tokens": 1000, "n": 8} can generate 8,000 tokens upstream, whatever the sandbox read of it."""
    proxy, path = _COMPLETION_ROUTES[route]
    raw = (ANSWERS / "openai-chat-stream.sse").read_bytes()
    upstream = _Upstream()
    gate = asyncio.Event()
    upstream.answers.append(_Answer(raw, content_type=_SSE, chunk=300, gate=gate))
    client = await _metered(aiohttp_client, model_server, proxy, upstream)
    body = {"model": "qwen", "messages": [], "stream": True, "max_tokens": 1000, **extra}
    resp = await client.post(path, json=body, headers=_AUTH)
    await resp.content.read(100)
    resp.close()
    gate.set()
    (counted,) = (await _settled(client))["models"].values()
    assert counted["output_tokens"] == 1000 * answers, f"{route}, {extra}: charged {counted['output_tokens']}"


@pytest.mark.parametrize("route", ["xai-proxy chat", "llm-proxy /v1", "llm-proxy /p/openai"])
@pytest.mark.parametrize(
    "extra",
    [{"n_predict": 100_000}, {"options": {"num_predict": -1}}, {"max_new_tokens": 100_000}],
    ids=["llama-cpp", "ollama-options", "tgi"],
)
async def test_a_chat_completion_naming_another_server_s_limit_is_refused_on_each_route(
    aiohttp_client, model_server, route, extra
):
    proxy, path = _COMPLETION_ROUTES[route]
    upstream = _Upstream()
    client = await _metered(aiohttp_client, model_server, proxy, upstream)
    body = {"model": "qwen", "messages": [], "max_tokens": 10, **extra}
    resp = await client.post(path, json=body, headers=_AUTH)
    assert resp.status == 400, f"{route} answered {resp.status} to {body}"
    assert upstream.calls == [], f"{route} forwarded {body}"
