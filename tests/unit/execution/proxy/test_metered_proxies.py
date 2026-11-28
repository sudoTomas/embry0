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


async def _xai(
    aiohttp_client: Any, upstream: _Upstream, *, enroll: dict[str, Any] | None = None, **kw: Any
) -> TestClient:
    client = await aiohttp_client(create_xai_proxy_app(_ADMIN, **kw))
    enrolled = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "sbx-1", "sandbox_token": _TOKEN, **(enroll or {})},
        headers=_ADMIN_HEADERS,
    )
    assert enrolled.status == 200, await enrolled.text()
    client.enrolled = await enrolled.json()  # type: ignore[attr-defined]
    assert (await client.post("/admin/token", json={"access_token": "at-1"}, headers=_ADMIN_HEADERS)).status == 200
    route_upstream(client.app, upstream)
    return client


async def _declare(client: TestClient, run_id: str = "run-1", sandbox_id: str = "sbx-1", **extra: Any) -> Any:
    return await client.post(
        "/admin/runs",
        json={"sandbox_id": sandbox_id, "run_id": run_id, "job_id": "job-1", **extra},
        headers=_ADMIN_HEADERS,
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


async def _local(
    aiohttp_client: Any, server: Any, *, providers: bool = False, enroll: dict[str, Any] | None = None, **kw: Any
) -> TestClient:
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
    enrolled = await client.post(
        "/admin/enroll", json={**body, "limits": LIMITS, **(enroll or {})}, headers=_ADMIN_HEADERS
    )
    assert enrolled.status == 200, await enrolled.text()
    client.enrolled = await enrolled.json()  # type: ignore[attr-defined]
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
    code = (await resp.json())["error"]["code"]
    assert code == "run_not_declared", f"a call outside a run was refused as {code}"
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


async def _metered(
    aiohttp_client: Any, model_server: Any, route: str, upstream: _Upstream, **declared: Any
) -> TestClient:
    """A metering proxy with sbx-1 enrolled and a run declared (with ``declared`` in its declaration), its upstream
    calls answered by ``upstream``."""
    if route == "xai-proxy":
        client = await _xai(aiohttp_client, upstream)
    else:
        client = await _local(aiohttp_client, model_server, providers=True)
        route_upstream(client.app, upstream)
    held = await _declare(client, **declared)
    assert held.status == 200, f"the run could not be declared ({held.status}): {await held.text()}"
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


@pytest.mark.parametrize("route", ["xai-proxy chat", "llm-proxy /v1", "llm-proxy /p/openai"])
@pytest.mark.parametrize(
    "raw",
    [
        b'{"model": "m", "max_tokens": 2000000, "n": 6}',
        b'{"model": "m", "max_tokens": 10, "n": 0}',
        b'{"model": "m", "max_tokens": 10, "n_cmpl": 1.5}',
    ],
    ids=["limit-times-answers-past-the-most", "no-answers", "answers-as-fraction"],
)
async def test_a_chat_completion_whose_answers_the_meter_cannot_count_is_refused(
    aiohttp_client, model_server, route, raw
):
    proxy, path = _COMPLETION_ROUTES[route]
    upstream = _Upstream()
    client = await _metered(aiohttp_client, model_server, proxy, upstream)
    resp = await client.post(path, data=raw, headers={**_AUTH, "Content-Type": "application/json"})
    assert resp.status == 400, f"{route} answered {resp.status} to {raw!r}"
    assert upstream.calls == [], f"{route} forwarded {raw!r}"


# --------------------------------------------------------------------------- a run's budget

#: A run's budget as the orchestrator declares it: $10 left of the job's, at $2 / $6 per million tokens.
BUDGET = {
    "rates": {"in": 2.0, "out": 6.0},
    "cache": {"read": 0.25, "write": 1.25},
    "purses": [{"key": "job:job-1", "limit": "job", "remaining_usd": 10.0, "members": []}],
}


def _budget(remaining: float) -> dict[str, Any]:
    """``BUDGET`` with ``remaining`` left in the job's purse."""
    return {**BUDGET, "purses": [{**BUDGET["purses"][0], "remaining_usd": remaining}]}


def _completion(route: str, **body: Any) -> dict[str, Any]:
    """A completion body for ``route``'s wire."""
    if route == "xai-proxy messages":
        return _messages(**body)
    return {"model": "qwen", "messages": [], **body}


def _refusal_code(route: str, answer: dict[str, Any]) -> Any:
    if route == "xai-proxy messages":
        return answer.get("type"), answer.get("error", {}).get("type")
    return "error", answer.get("error", {}).get("code")


@pytest.mark.parametrize("route", sorted(_COMPLETION_ROUTES))
async def test_a_call_past_its_run_s_budget_is_refused_and_never_sent(aiohttp_client, model_server, route):
    """A job near its cap: $0.001 left, and a call that could cost $0.006 at its ceiling."""
    proxy, path = _COMPLETION_ROUTES[route]
    upstream = _Upstream()
    client = await _metered(aiohttp_client, model_server, proxy, upstream, budget=_budget(0.001))
    resp = await client.post(path, json=_completion(route, max_tokens=1000), headers=_AUTH)
    assert resp.status == 402, f"{route} answered {resp.status} to a call past the run's budget: {await resp.text()}"
    answer = await resp.json(content_type=None)
    assert _refusal_code(route, answer) == ("error", "budget_exceeded"), f"{route} refused it as {answer}"
    assert "the job's budget" in json.dumps(answer), answer
    assert upstream.calls == [], f"{route} forwarded a call past the run's budget"
    tally = await _run(client)
    assert (tally.get("budget_refused_by"), tally["models"]) == ({"job": 1}, {}), tally


@pytest.mark.parametrize("route", ["xai-proxy chat", "llm-proxy /v1", "llm-proxy /p/openai"])
async def test_a_completion_asking_for_78_answers_and_no_limit_is_refused_with_10_dollars_left(
    aiohttp_client, model_server, route
):
    """9,984,000 output tokens at its ceiling, about $60 at $6 a million: one hang-up would charge it all."""
    proxy, path = _COMPLETION_ROUTES[route]
    upstream = _Upstream()
    client = await _metered(aiohttp_client, model_server, proxy, upstream, budget=BUDGET)
    resp = await client.post(path, json=_completion(route, stream=True, n=78), headers=_AUTH)
    assert resp.status == 402, f"{route} answered {resp.status} to n=78 with $10 left"
    assert upstream.calls == [], f"{route} forwarded n=78 with $10 left"


@pytest.mark.parametrize("route", sorted(_COMPLETION_ROUTES))
async def test_twenty_calls_in_flight_at_a_run_s_end_stay_within_its_budget(aiohttp_client, model_server, route):
    """Each could cost $1.20 at its ceiling: eight go, the other twelve are refused, and all eight hung up stay in $10."""
    proxy, path = _COMPLETION_ROUTES[route]
    raw = (
        ANSWERS / ("anthropic-stream.sse" if route == "xai-proxy messages" else "openai-chat-stream.sse")
    ).read_bytes()
    upstream = _Upstream()
    gate = asyncio.Event()
    upstream.answers.extend(_Answer(raw, content_type=_SSE, chunk=300, gate=gate) for _ in range(20))
    client = await _metered(aiohttp_client, model_server, proxy, upstream, budget=BUDGET)
    held = []
    for _ in range(20):
        resp = await client.post(path, json=_completion(route, stream=True, max_tokens=200_000), headers=_AUTH)
        held.append(resp)
    statuses = sorted(r.status for r in held)
    assert statuses == [200] * 8 + [402] * 12, f"{route}: {statuses}"
    for resp in held:
        resp.close()  # the sandbox hangs up on every one
    gate.set()
    tally = await _settled(client)
    (counted,) = tally["models"].values()
    charged = (counted["input_tokens"] * 2.0 + counted["output_tokens"] * 6.0) / 1e6
    assert charged <= 10.0, f"{route}: the run was charged ${charged:.2f} of $10"
    assert counted["output_tokens"] == 8 * 200_000, counted


@pytest.mark.parametrize("route", sorted(_COMPLETION_ROUTES))
async def test_a_call_within_its_run_s_budget_goes_and_is_charged(aiohttp_client, model_server, route):
    proxy, path = _COMPLETION_ROUTES[route]
    name = "anthropic-message.json" if route == "xai-proxy messages" else "openai-chat.json"
    upstream = _Upstream()
    upstream.answers.append(_Answer((ANSWERS / name).read_bytes()))
    client = await _metered(aiohttp_client, model_server, proxy, upstream, budget=BUDGET)
    resp = await client.post(path, json=_completion(route, stream=False, max_tokens=4096), headers=_AUTH)
    assert resp.status == 200, f"{route} answered {resp.status} to a call within the run's budget"
    await resp.read()
    tally = await _settled(client)
    assert (tally.get("budget_refused"), sum(m["requests"] for m in tally["models"].values())) == (0, 1), tally
    assert 0 < tally["budget"]["committed_usd"] < 10.0, tally["budget"]


@pytest.mark.parametrize("route", ["xai-proxy chat", "llm-proxy /v1", "llm-proxy /p/openai"])
async def test_a_run_declared_without_a_budget_is_checked_against_nothing(aiohttp_client, model_server, route):
    """An older orchestrator's declaration, a free run or one with no cap set: n=78 goes, as before."""
    proxy, path = _COMPLETION_ROUTES[route]
    upstream = _Upstream()
    upstream.answers.append(_Answer((ANSWERS / "openai-chat.json").read_bytes()))
    client = await _metered(aiohttp_client, model_server, proxy, upstream)
    resp = await client.post(path, json=_completion(route, n=78), headers=_AUTH)
    assert resp.status == 200, f"{route} answered {resp.status} with no budget declared"
    assert len(upstream.calls) == 1


@pytest.mark.parametrize("proxy", ["xai-proxy", "llm-proxy /v1"])
async def test_a_declaration_s_budget_is_echoed_and_one_that_is_not_a_budget_is_refused(
    aiohttp_client, model_server, proxy
):
    upstream = _Upstream()
    client = (
        await _xai(aiohttp_client, upstream) if proxy == "xai-proxy" else await _local(aiohttp_client, model_server)
    )
    held = await _declare(client, budget=BUDGET)
    assert held.status == 200
    assert (await held.json()).get("budget") == {"purses": {"job:job-1": 10.0}}, "the budget was not echoed"
    refused = await _declare(client, run_id="run-2", budget=_budget(-1))
    assert refused.status == 400, f"a budget of -$1 was taken (HTTP {refused.status})"


@pytest.mark.parametrize("route", ["xai-proxy chat", "llm-proxy /v1"])
async def test_two_runs_of_a_job_at_one_proxy_share_its_purse(aiohttp_client, model_server, route):
    """A QA fan-out's apps, each in its own sandbox: what one has committed the other cannot spend."""
    proxy, path = _COMPLETION_ROUTES[route]
    upstream = _Upstream()
    gate = asyncio.Event()
    raw = (ANSWERS / "openai-chat-stream.sse").read_bytes()
    upstream.answers.append(_Answer(raw, content_type=_SSE, chunk=300, gate=gate))
    client = await _metered(aiohttp_client, model_server, proxy, upstream, budget=_budget(2.0))
    token_b = "second-sandbox-bearer-at-least-32-chars"
    enrollment: dict[str, Any] = {"sandbox_id": "sbx-2", "sandbox_token": token_b}
    if proxy != "xai-proxy":
        server = {
            "credential_id": "cred-a",
            "upstream": f"http://127.0.0.1:{model_server.port}/v1",
            "api_key": "",
            "address": "127.0.0.1",
        }
        enrollment.update(local_llm=server, limits=LIMITS)
    enrolled = await client.post("/admin/enroll", json=enrollment, headers=_ADMIN_HEADERS)
    assert enrolled.status == 200, await enrolled.text()
    second = {**_budget(2.0), "purses": [{**_budget(2.0)["purses"][0], "members": ["run-1"]}]}
    assert (await _declare(client, run_id="run-2", sandbox_id="sbx-2", budget=second)).status == 200
    body = _completion(route, stream=True, max_tokens=200_000)
    first = await client.post(path, json=body, headers=_AUTH)
    assert first.status == 200
    refused = await client.post(path, json=body, headers={"Authorization": f"Bearer {token_b}"})
    assert refused.status == 402, f"{route}: the second run spent what the first had committed ({refused.status})"
    first.close()
    gate.set()


@pytest.mark.parametrize("proxy", ["xai-proxy", "llm-proxy /v1"])
async def test_a_purse_s_figures_are_updated_and_a_bad_update_refused(aiohttp_client, model_server, proxy):
    upstream = _Upstream()
    client = await _metered(aiohttp_client, model_server, proxy, upstream, budget=_budget(10.0))
    purse = {"key": "job:job-1", "limit": "job", "remaining_usd": 0.5, "members": ["run-1"]}
    updated = await client.put("/admin/purses", json={"purses": [purse], "seq": 10**18}, headers=_ADMIN_HEADERS)
    assert updated.status == 200, await updated.text()
    assert (await updated.json())["purses"] == {"job:job-1": 0.5}
    bad = await client.put("/admin/purses", json={"purses": [{**purse, "remaining_usd": -1}]}, headers=_ADMIN_HEADERS)
    assert bad.status == 400
    assert (await client.put("/admin/purses", json={"purses": [purse]})).status in (401, 403), "no admin token"


@pytest.mark.parametrize("route", ["llm-proxy /v1", "llm-proxy /p/openai", "xai-proxy chat"])
async def test_hung_up_calls_after_a_cached_one_are_charged_no_more_than_the_purse(aiohttp_client, model_server, route):
    """One 600 KB call answered whole (mostly cache reads), then twelve held open and hung up: each was let through at
    the run's cached cost a byte and charged its input at two bytes a token uncached, past the $2 purse."""
    proxy, path = _COMPLETION_ROUTES[route]
    content = "x" * 600_000
    cached = {
        "id": "c1",
        "model": "qwen",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
        "usage": {
            "prompt_tokens": 150_000,
            "completion_tokens": 1_500,
            "prompt_tokens_details": {"cached_tokens": 149_000},
        },
    }
    upstream = _Upstream()
    upstream.answers.append(_Answer(json.dumps(cached).encode()))
    gate = asyncio.Event()
    raw = (ANSWERS / "openai-chat-stream.sse").read_bytes()
    upstream.answers.extend(_Answer(raw, content_type=_SSE, chunk=300, gate=gate) for _ in range(12))
    budget = {**_budget(2.0), "cache": {"read": 0.1, "write": 1.25}}
    client = await _metered(aiohttp_client, model_server, proxy, upstream, budget=budget)
    body = {"model": "qwen", "messages": [{"role": "user", "content": content}], "max_tokens": 32_000}
    await (await client.post(path, json=body, headers=_AUTH)).read()
    held = [await client.post(path, json={**body, "stream": True}, headers=_AUTH) for _ in range(12)]
    let_through = sum(r.status == 200 for r in held)
    for resp in held:
        resp.close()  # the sandbox hangs up on every one
    gate.set()
    tally = await _settled(client)
    charged = sum(
        (m["input_tokens"] * 2.0 + m["cache_read_tokens"] * 2.0 * 0.1 + m["output_tokens"] * 6.0) / 1e6
        for m in tally["models"].values()
    )
    assert 0 < let_through < 12, f"{route}: {let_through} of 12 let through"
    assert charged <= 2.0, f"{route}: {let_through} hung-up calls were charged ${charged:.2f} on a $2 purse"


def _by_url(route: str, index: int) -> dict[str, Any]:
    """An image the provider fetches, in ``route``'s wire."""
    if route == "xai-proxy messages":
        url = f"https://images.example.test/{index:04d}/{'p' * 6}.png"
        return {"type": "image", "source": {"type": "url", "url": url}}
    return {"type": "image_url", "image_url": {"url": f"https://images.example.test/{index:04d}/{'p' * 20}.png"}}


@pytest.mark.parametrize("route", sorted(_COMPLETION_ROUTES))
async def test_sixty_calls_of_200_images_by_url_are_refused_and_never_sent(aiohttp_client, model_server, route):
    """About 20 KB each, so the check would hold about 20,000 input tokens for a call billed about 220,000 (some
    1,100 an image): sixty at once would charge $26 to a $10 purse. None is sent, in the wire's error shape."""
    proxy, path = _COMPLETION_ROUTES[route]
    upstream = _Upstream()
    client = await _metered(aiohttp_client, model_server, proxy, upstream, budget=BUDGET)
    parts = [{"type": "text", "text": "describe these"}, *(_by_url(route, i) for i in range(200))]
    body = _completion(route, max_tokens=1000, messages=[{"role": "user", "content": parts}])
    assert 18_000 < len(json.dumps(body)) < 22_000, f"the probe's body is {len(json.dumps(body))} bytes"
    answers = await asyncio.gather(*(client.post(path, json=body, headers=_AUTH) for _ in range(60)))
    statuses = sorted({resp.status for resp in answers})
    assert statuses == [400], f"{route} answered {statuses}"
    answer = await answers[0].json(content_type=None)
    if route == "xai-proxy messages":
        error = answer.get("error", {})
        shape = (answer.get("type"), error.get("type"), str(error.get("message", "")).split(":")[0])
        assert shape == ("error", "invalid_request_error", "unmetered_content"), f"{route} refused it as {answer}"
    else:
        assert _refusal_code(route, answer) == ("error", "unmetered_content"), f"{route} refused it as {answer}"
    assert upstream.calls == [], f"{route} forwarded {len(upstream.calls)} calls of images by URL"
    assert (await _run(client))["models"] == {}, "a refused call was counted"


@pytest.mark.parametrize("route", sorted(_COMPLETION_ROUTES))
async def test_a_call_with_a_screenshot_given_inline_goes(aiohttp_client, model_server, route):
    """A QA agent's screenshot goes inline, in its tool's result: the call goes and is charged."""
    proxy, path = _COMPLETION_ROUTES[route]
    name = "anthropic-message.json" if route == "xai-proxy messages" else "openai-chat.json"
    upstream = _Upstream()
    upstream.answers.append(_Answer((ANSWERS / name).read_bytes(), content_type="application/json"))
    client = await _metered(aiohttp_client, model_server, proxy, upstream, budget=BUDGET)
    if route == "xai-proxy messages":
        shot = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}}
        content: Any = [{"type": "tool_result", "tool_use_id": "tu1", "content": [shot]}]
    else:
        content = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}]
    body = _completion(route, max_tokens=1000, messages=[{"role": "user", "content": content}])
    resp = await client.post(path, json=body, headers=_AUTH)
    assert resp.status == 200, f"{route} answered {resp.status}: {await resp.text()}"
    assert len(upstream.calls) == 1, f"{route} sent {len(upstream.calls)} calls"


def _pdf_as_image(route: str) -> dict[str, Any]:
    """A PDF named an inline image, in ``route``'s wire: billed by the page, not as an image."""
    if route == "xai-proxy messages":
        return {"type": "image", "source": {"type": "base64", "media_type": "application/pdf", "data": "JVBERi0x"}}
    return {"type": "image_url", "image_url": {"url": "data:application/pdf;base64,JVBERi0x"}}


@pytest.mark.parametrize("route", sorted(_COMPLETION_ROUTES))
async def test_a_pdf_named_an_inline_image_is_refused_and_never_sent(aiohttp_client, model_server, route):
    proxy, path = _COMPLETION_ROUTES[route]
    upstream = _Upstream()
    client = await _metered(aiohttp_client, model_server, proxy, upstream, budget=BUDGET)
    body = _completion(route, max_tokens=1000, messages=[{"role": "user", "content": [_pdf_as_image(route)]}])
    resp = await client.post(path, json=body, headers=_AUTH)
    assert resp.status == 400, f"{route} answered {resp.status} to a PDF named an image"
    assert "unmetered_content" in await resp.text(), await resp.text()
    assert upstream.calls == [], f"{route} forwarded a PDF named an image"


@pytest.mark.parametrize("route", ["xai-proxy chat", "llm-proxy /v1", "llm-proxy /p/openai"])
async def test_a_fallback_model_list_is_refused_and_never_sent(aiohttp_client, model_server, route):
    """An ``:online`` id among a router's fallback models searches the web: the refusal of one as the model, kept."""
    proxy, path = _COMPLETION_ROUTES[route]
    upstream = _Upstream()
    client = await _metered(aiohttp_client, model_server, proxy, upstream, budget=BUDGET)
    body = _completion(route, max_tokens=1000, models=["openai/gpt-6:online"])
    resp = await client.post(path, json=body, headers=_AUTH)
    assert resp.status == 400, f"{route} answered {resp.status} to a fallback model list"
    assert _refusal_code(route, await resp.json(content_type=None)) == ("error", "unmetered_content")
    assert upstream.calls == [], f"{route} forwarded a fallback model list"


# --------------------------------------------------------------------------- a run held to its provider and model


def _model_of(route: str) -> str:
    return "grok-4.7" if route == "xai-proxy messages" else "qwen"


@pytest.mark.parametrize("route", sorted(_COMPLETION_ROUTES))
async def test_a_call_naming_a_model_its_run_was_not_declared_with_is_refused_and_never_sent(
    aiohttp_client, model_server, route
):
    """Checked and charged at the run's rates, a dearer model on the same route would cost more than both say."""
    proxy, path = _COMPLETION_ROUTES[route]
    name = "anthropic-message.json" if route == "xai-proxy messages" else "openai-chat.json"
    upstream = _Upstream()
    upstream.answers.append(_Answer((ANSWERS / name).read_bytes()))
    client = await _metered(aiohttp_client, model_server, proxy, upstream, budget=BUDGET, models=[_model_of(route)])
    dearer = await client.post(path, json=_completion(route, max_tokens=100, model="pricier-model"), headers=_AUTH)
    assert dearer.status == 403, f"{route} answered {dearer.status} to a call naming another model"
    answer = await dearer.json(content_type=None)
    if route == "xai-proxy messages":
        error = answer.get("error", {})
        shape = (answer.get("type"), error.get("type"), str(error.get("message", "")).split(":")[0])
        assert shape == ("error", "permission_error", "model_not_declared"), f"{route} refused it as {answer}"
    else:
        assert _refusal_code(route, answer) == ("error", "model_not_declared"), f"{route} refused it as {answer}"
    assert upstream.calls == [], f"{route} forwarded a call naming another model"
    held = await client.post(path, json=_completion(route, stream=False, max_tokens=100), headers=_AUTH)
    assert held.status == 200, f"{route} answered {held.status} to a call naming the run's own model"
    await held.read()
    assert sum(m["requests"] for m in (await _settled(client))["models"].values()) == 1


async def test_a_declaration_says_it_holds_the_run_to_its_models(aiohttp_client):
    client = await _xai(aiohttp_client, _Upstream())
    resp = await _declare(client, provider="xai", models=["grok-4.7"])
    assert resp.status == 200, await resp.text()
    assert (await resp.json()).get("models") == ["grok-4.7"], "the proxy did not say it holds the run to its model"
    for bad in ([], "grok-4.7", [""], ["a", "a"], [7], [f"m{i}" for i in range(9)]):
        refused = await _declare(client, run_id="run-2", provider="xai", models=bad)
        assert refused.status == 400, f"a declaration naming models {bad!r} was taken ({refused.status})"


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/v1/messages"), ("POST", "/v1/chat/completions"), ("GET", "/v1/models")],
)
async def test_the_xai_proxy_refuses_a_sandbox_whose_run_is_on_another_provider(aiohttp_client, method, path):
    """Another provider's run is charged from its own provider's proxy: what it spent here would be counted under it
    and never charged."""
    upstream = _Upstream()
    client = await _xai(aiohttp_client, upstream)
    held = await _declare(client, provider="custom")
    assert held.status == 200, await held.text()
    body = _messages(max_tokens=100) if path == "/v1/messages" else {"model": "grok-4.7", "max_tokens": 100}
    resp = await client.request(method, path, json=body if method == "POST" else None, headers=_AUTH)
    assert resp.status == 403, f"{method} {path} answered {resp.status} for a run on another provider"
    assert "provider_not_declared" in await resp.text(), await resp.text()
    assert upstream.calls == [], f"{method} {path} went upstream for a run on another provider"


async def test_the_xai_proxy_lets_a_run_on_xai_through(aiohttp_client):
    upstream = _Upstream()
    upstream.answers.append(_Answer((ANSWERS / "anthropic-message.json").read_bytes()))
    client = await _xai(aiohttp_client, upstream)
    held = await _declare(client, provider="xai", models=["grok-4.7"])
    assert held.status == 200, await held.text()
    resp = await client.post("/v1/messages", json=_messages(max_tokens=100, stream=False), headers=_AUTH)
    assert resp.status == 200, f"a call of a run on xai answered {resp.status}: {await resp.text()}"
    assert len(upstream.calls) == 1


# --------------------------------------------------------------------------- no call outside a run


#: The metered routes that took a call outside a run before the setting: each proxy, its path and its wire's call.
_OUTSIDE_A_RUN = {
    "xai-proxy messages": ("xai-proxy", "/v1/messages"),
    "xai-proxy chat": ("xai-proxy", "/v1/chat/completions"),
    "llm-proxy /v1": ("llm-proxy /v1", "/v1/chat/completions"),
}


async def _enrolled(aiohttp_client: Any, model_server: Any, proxy: str, upstream: _Upstream, enroll: Any) -> Any:
    if proxy == "xai-proxy":
        return await _xai(aiohttp_client, upstream, enroll=enroll)
    client = await _local(aiohttp_client, model_server, enroll=enroll)
    route_upstream(client.app, upstream)
    return client


def _outside_a_run_call(route: str) -> dict[str, Any]:
    return (
        _messages(max_tokens=100, stream=False) if route == "xai-proxy messages" else {"model": "qwen", "messages": []}
    )


@pytest.mark.parametrize("route", sorted(_OUTSIDE_A_RUN))
async def test_a_sandbox_enrolled_to_refuse_calls_outside_runs_has_one_refused_and_never_sent(
    aiohttp_client, model_server, route
):
    """No run, no charge to anyone: refused in the wire's shape, sent nowhere, counted nowhere."""
    proxy, path = _OUTSIDE_A_RUN[route]
    upstream = _Upstream()
    client = await _enrolled(aiohttp_client, model_server, proxy, upstream, {"require_run": True})
    assert client.enrolled.get("require_run") is True, f"the enrollment did not say it holds: {client.enrolled}"
    resp = await client.post(path, json=_outside_a_run_call(route), headers=_AUTH)
    assert resp.status == 403, f"{route} answered {resp.status} to a call outside a run"
    answer = await resp.json(content_type=None)
    if route == "xai-proxy messages":
        error = answer.get("error", {})
        shape = (error.get("type"), str(error.get("message", "")).split(":")[0])
        assert shape == ("permission_error", "run_not_declared"), f"{route} refused it as {answer}"
    else:
        assert answer.get("error", {}).get("code") == "run_not_declared", f"{route} refused it as {answer}"
    assert upstream.calls == [] and model_server.seen == [], f"{route} forwarded a call outside a run"
    assert [t for t in (await _meter(client))["tallies"] if t["sandbox_id"] == "sbx-1"] == [], "it was counted"
    assert (await _declare(client)).status == 200
    upstream.answers.append(
        _Answer(
            (ANSWERS / ("anthropic-message.json" if route == "xai-proxy messages" else "openai-chat.json")).read_bytes()
        )
    )
    model_server.answers.append(("application/json", (ANSWERS / "openai-chat.json").read_bytes()))
    during = await client.post(path, json=_outside_a_run_call(route), headers=_AUTH)
    assert during.status == 200, f"{route} answered {during.status} to a call of a declared run"


@pytest.mark.parametrize("route", sorted(_OUTSIDE_A_RUN))
@pytest.mark.parametrize("enroll", [{"require_run": False}, {}], ids=["told-not-to", "an-older-orchestrator"])
async def test_a_sandbox_not_held_to_runs_has_a_call_outside_one_counted_under_no_run(
    aiohttp_client, model_server, route, enroll
):
    proxy, path = _OUTSIDE_A_RUN[route]
    upstream = _Upstream()
    upstream.answers.append(
        _Answer(
            (ANSWERS / ("anthropic-message.json" if route == "xai-proxy messages" else "openai-chat.json")).read_bytes()
        )
    )
    model_server.answers.append(("application/json", (ANSWERS / "openai-chat.json").read_bytes()))
    client = await _enrolled(aiohttp_client, model_server, proxy, upstream, enroll)
    resp = await client.post(path, json=_outside_a_run_call(route), headers=_AUTH)
    assert resp.status == 200, f"{route} answered {resp.status}: {await resp.text()}"
    await resp.read()
    unattributed = [t for t in (await _meter(client))["tallies"] if t["run_id"] is None]
    assert len(unattributed) == 1, f"the call was not counted under no run: {unattributed}"


@pytest.mark.parametrize(
    ("proxy", "method", "path"),
    [
        ("xai-proxy", "GET", "/v1/models"),
        ("xai-proxy", "POST", "/v1/messages/count_tokens"),
        ("llm-proxy /v1", "GET", "/v1/models"),
    ],
)
async def test_what_bills_nothing_stays_open_outside_a_run(aiohttp_client, model_server, proxy, method, path):
    upstream = _Upstream()
    upstream.answers.append(_Answer(b'{"data": [], "input_tokens": 3}'))
    client = await _enrolled(aiohttp_client, model_server, proxy, upstream, {"require_run": True})
    body = {"model": "grok-4.7", "messages": []} if method == "POST" else None
    resp = await client.request(method, path, json=body, headers=_AUTH)
    assert resp.status == 200, f"{method} {path} answered {resp.status} outside a run: {await resp.text()}"


@pytest.mark.parametrize("proxy", ["xai-proxy", "llm-proxy /v1"])
async def test_an_enrollment_again_says_afresh_whether_the_sandbox_is_held_to_runs(aiohttp_client, model_server, proxy):
    upstream = _Upstream()
    client = await _enrolled(aiohttp_client, model_server, proxy, upstream, {"require_run": True})
    again = {"sandbox_id": "sbx-1", "sandbox_token": _TOKEN}
    if proxy != "xai-proxy":
        again.update(
            local_llm={
                "credential_id": "cred-a",
                "upstream": f"http://127.0.0.1:{model_server.port}/v1",
                "api_key": "",
                "address": "127.0.0.1",
            },
            limits=LIMITS,
        )
    assert (await client.post("/admin/enroll", json=again, headers=_ADMIN_HEADERS)).status == 200
    upstream.answers.append(_Answer((ANSWERS / "openai-chat.json").read_bytes()))
    model_server.answers.append(("application/json", (ANSWERS / "openai-chat.json").read_bytes()))
    resp = await client.post("/v1/chat/completions", json={"model": "qwen", "messages": []}, headers=_AUTH)
    assert resp.status == 200, f"an enrollment again that does not say kept the sandbox held: {resp.status}"
    for bad in ("true", 1, [True]):
        refused = await client.post("/admin/enroll", json={**again, "require_run": bad}, headers=_ADMIN_HEADERS)
        assert refused.status == 400, f"an enrollment with require_run {bad!r} was taken ({refused.status})"


# --------------------------------------------------------------------------- what the xai-proxy forwards besides calls


async def test_a_deferred_completion_is_refused_and_never_sent(aiohttp_client):
    """Answered with a request id and fetched later by a read, its usage would never reach the meter, and its purse
    would let it go at once."""
    upstream = _Upstream()
    client = await _xai(aiohttp_client, upstream)
    assert (await _declare(client, provider="xai", budget=BUDGET)).status == 200
    body = {"model": "grok-4.7", "messages": [], "max_tokens": 32000, "deferred": True}
    resp = await client.post("/v1/chat/completions", json=body, headers=_AUTH)
    assert resp.status == 400, f"a deferred completion answered {resp.status}"
    assert _refusal_code("xai-proxy chat", await resp.json(content_type=None)) == ("error", "unmetered_content")
    assert upstream.calls == [], "a deferred completion was forwarded"


@pytest.mark.parametrize(
    "path",
    ["/v1/chat/deferred-completion/req-1", "/v1/api-key", "/v1/language-models", "/v1/messages", "/v1/models/a/b"],
)
@pytest.mark.parametrize("declared", [True, False], ids=["in-a-run", "outside-a-run"])
async def test_the_xai_proxy_forwards_no_read_but_the_model_listing(aiohttp_client, path, declared):
    """A deferred completion's result and the account's own reads are no agent's: refused, in a run or out of one."""
    upstream = _Upstream()
    client = await _xai(aiohttp_client, upstream)
    if declared:
        assert (await _declare(client, provider="xai")).status == 200
    for method in ("GET", "HEAD"):
        resp = await client.request(method, path, headers=_AUTH)
        assert resp.status == 404, f"{method} {path} answered {resp.status}"
    assert upstream.calls == [], f"{path} was forwarded"


@pytest.mark.parametrize("path", ["/v1/models", "/v1/models/grok-4.7"])
async def test_the_xai_proxy_forwards_the_model_listing(aiohttp_client, path):
    upstream = _Upstream()
    upstream.answers.append(_Answer(b'{"data": []}'))
    client = await _xai(aiohttp_client, upstream)
    resp = await client.get(path, headers=_AUTH)
    assert resp.status == 200, f"GET {path} answered {resp.status}: {await resp.text()}"
    assert len(upstream.calls) == 1


@pytest.mark.parametrize("path", ["/v1/files", "/v1/api-key", "/v1/chat/deferred-completion/req-1", "/v1/embeddings"])
async def test_the_llm_proxy_answers_only_its_two_routes(aiohttp_client, model_server, path):
    client = await _local(aiohttp_client, model_server)
    for method in ("GET", "POST"):
        resp = await client.request(method, path, json={} if method == "POST" else None, headers=_AUTH)
        assert resp.status in (404, 405), f"{method} {path} answered {resp.status}"
    assert model_server.seen == [], f"{path} reached the server"


@pytest.mark.parametrize("path", ["/v1/models/%2e%2e", "/v1/models/%2E%2E", "/v1/models/%2e", "/v1/models/.hidden"])
async def test_a_model_read_whose_id_is_dots_is_refused(aiohttp_client, path):
    """An encoded ``..`` reaches the route as the id "..", and the client upstream would resolve it to another path."""
    from yarl import URL

    upstream = _Upstream()
    upstream.answers.append(_Answer(b'{"data": []}'))
    client = await _xai(aiohttp_client, upstream)
    resp = await client.get(URL(path, encoded=True), headers=_AUTH)
    assert resp.status == 404, f"GET {path} answered {resp.status}"
    assert upstream.calls == [], f"GET {path} was forwarded as {upstream.calls}"
