"""The xai- and local-llm-proxy meter every model call they forward, under the run the orchestrator declared for
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

import httpx
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient

from embry0.execution.proxy.local_llm_proxy import create_local_llm_proxy_app
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
    ) -> None:
        self.status_code = status
        self.headers = {"content-type": content_type}
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


async def test_a_stream_cut_midway_keeps_its_input_and_estimates_the_rest(aiohttp_client):
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
    assert model["output_tokens"] >= 1


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


async def test_a_client_that_goes_away_midstream_leaves_an_estimate_not_nothing(aiohttp_client):
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
    assert model["output_tokens"] >= 1


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


# --------------------------------------------------------------------------- local-llm-proxy


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


async def _local(aiohttp_client: Any, server: Any, **kw: Any) -> TestClient:
    client = await aiohttp_client(create_local_llm_proxy_app(_ADMIN, **kw))
    server_of_sandbox = {
        "credential_id": "cred-a",
        "upstream": f"http://127.0.0.1:{server.port}/v1",
        "api_key": "",
        "address": "127.0.0.1",
    }
    enrolled = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "sbx-1", "sandbox_token": _TOKEN, "local_llm": server_of_sandbox},
        headers=_ADMIN_HEADERS,
    )
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
