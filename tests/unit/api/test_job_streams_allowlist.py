"""A job's streams, and the tokens that open them, read a job only within the caller's repository allowlist.

On the real app over a real database (:mod:`tests.fixtures.job_reads`). A
browser opens a job's event stream (a WebSocket), its QA workspace's live
log, or its QA run's events with a short-lived token it trades its key for.
A key with an allowlist that asks for a token for another repository's job
gets one shaped like any other, minted as for a job that does not exist, and
the stream it opens reads nothing: it answers as the stream of a job that
does not exist, which is also what every caller's stream of a missing job
answers. A stream opened with the key itself, not a token, holds the key to
the same rule. A key without a list streams the job, so the checks measure
something, and the limited key still streams its own job.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from embry0.api.ws.streaming import job_events
from embry0.storage.database import DatabasePool
from tests.fixtures.job_reads import JOB, MARK, MISSING, job_reads_app, same_answer
from tests.fixtures.repository_writes import COVERED, UNCOVERED, call
from tests.fixtures.routes import app_routes
from tests.unit.api.test_repository_writes_inventory import READS, _app

pytestmark = pytest.mark.requires_postgres

#: Each route that mints a token for a job's stream, with the stream it opens.
TOKEN_ROUTES: dict[str, str] = {
    "/api/v1/jobs/{job_id}/events-token": "the job's events, WS /ws/jobs/{job_id}/events",
    "/api/v1/jobs/{job_id}/qa/log-stream-token": "its QA workspace's log, GET /jobs/{job_id}/artifacts/logs/{service}",
    "/api/v1/qa/runs/{run_id}/events-token": "its QA run's events, GET /qa/runs/{run_id}/events",
}
_TOKEN = re.compile(r"^v1\.\d+\.[0-9a-f]{64}$")


def test_every_route_that_mints_a_job_s_stream_token_is_here() -> None:
    minting = {r.path for r in app_routes(_app()) if r.path.endswith("-token") and r.path.startswith("/api/v1/")}
    assert minting == set(TOKEN_ROUTES), f"routes minting a stream token: {sorted(minting)}"
    assert set(TOKEN_ROUTES) <= set(READS), "each is a read sent as a POST in the write inventory"


def _url(path: str, job: str) -> str:
    return path.replace("{job_id}", job).replace("{run_id}", job)


class _Tokens:
    """The tokens minted on one route, by name, for the check that each looks like any other."""

    def __init__(self, app: FastAPI, path: str) -> None:
        self.app, self.path = app, path
        self.minted: dict[str, Any] = {}

    async def mint(self, name: str, job: str, key: str) -> str:
        response = await call(self.app, "POST", _url(self.path, job), key)
        self.minted[name] = response
        return str(response.json().get("token", ""))

    def shapes(self) -> dict[str, Any]:
        def shape(response: Any) -> Any:
            body = response.json()
            token = str(body.get("token", ""))
            return response.status_code, tuple(sorted(body)), body.get("expires_in"), bool(_TOKEN.match(token))

        return {name: shape(response) for name, response in self.minted.items()}

    def assert_alike(self) -> None:
        shapes = self.shapes()
        assert len(set(shapes.values())) == 1 and next(iter(shapes.values()))[::3] == (200, True), (
            f"{self.path}: each token looks like any other, another repository's job's as a missing one's: {shapes}"
        )


class _Socket:
    """The server's end of a WebSocket: records what the route does with it; the client leaves on :attr:`leave`."""

    def __init__(self, app: FastAPI, token: str) -> None:
        self.app = app
        self.offered = f"embry0.token.{token}"
        self.scope = {"type": "websocket", "subprotocols": [self.offered], "headers": []}
        self.client = None
        self.query_params: dict[str, str] = {}
        self.cookies: dict[str, str] = {}
        self.did: list[Any] = []
        self.leave = asyncio.Event()

    async def accept(self, subprotocol: str | None = None) -> None:
        self.did.append(("accept", subprotocol == self.offered))

    async def send_json(self, data: Any) -> None:
        self.did.append(("send", data))

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.did.append(("close", code))

    async def receive(self) -> dict[str, Any]:
        await self.leave.wait()
        return {"type": "websocket.disconnect"}


async def _ws(app: FastAPI, job: str, token: str) -> list[Any]:
    """What the job's event stream does for a client holding ``token``: an event is published live on the job, if it
    exists, while the client is there."""
    socket = _Socket(app, token)
    task = asyncio.create_task(job_events(socket, job))  # type: ignore[arg-type]
    await asyncio.sleep(0.1)
    if job != MISSING:
        await app.state.event_bus.publish(job, {"type": "progress", "message": f"live {job}", "event_seq": 10**9})
    await asyncio.sleep(0.1)
    socket.leave.set()
    await asyncio.wait_for(task, 5)
    return socket.did


async def test_the_events_of_another_repository_s_job_stream_as_a_missing_job_s(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with job_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        tokens = _Tokens(app, "/api/v1/jobs/{job_id}/events-token")

        async def stream(name: str, job: str, key: str) -> list[Any]:
            return await _ws(app, job, await tokens.mint(name, job, key))

        shown = {
            "uncovered": await stream("uncovered", JOB[UNCOVERED], keys.limited),
            "missing": await stream("missing", MISSING, keys.limited),
            "missing to everyone": await stream("missing to everyone", MISSING, keys.open),
            "everyone": await stream("everyone", JOB[UNCOVERED], keys.open),
            "own": await stream("own", JOB[COVERED], keys.limited),
        }
    tokens.assert_alike()
    nothing = [("accept", True)]
    assert shown["uncovered"] == shown["missing"] == shown["missing to everyone"] == nothing, (
        f"the limited key's stream of {UNCOVERED}'s job is a missing job's, which sends nothing: {shown}"
    )
    assert MARK[UNCOVERED] in str(shown["everyone"]) and shown["everyone"][-1] == ("close", 1000), shown["everyone"]
    assert MARK[COVERED] in str(shown["own"]), f"the limited key streams its own job: {shown['own']}"


async def _get(app: FastAPI, url: str) -> Any:
    """``url`` read with no credential but what it carries."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(url)


async def test_the_live_log_of_another_repository_s_job_streams_as_a_missing_job_s(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with job_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        tokens = _Tokens(app, "/api/v1/jobs/{job_id}/qa/log-stream-token")

        def log(job: str) -> str:
            service = MARK[COVERED] if job == JOB[COVERED] else MARK[UNCOVERED]
            return f"/api/v1/jobs/{job}/artifacts/logs/{service}?follow=true"

        async def by_token(name: str, job: str, key: str) -> Any:
            return await _get(app, f"{log(job)}&token={await tokens.mint(name, job, key)}")

        shown = {
            "uncovered": await by_token("uncovered", JOB[UNCOVERED], keys.limited),
            "missing": await by_token("missing", MISSING, keys.limited),
            "everyone": await by_token("everyone", JOB[UNCOVERED], keys.open),
            "own": await by_token("own", JOB[COVERED], keys.limited),
            "uncovered by key": await call(app, "GET", log(JOB[UNCOVERED]), keys.limited),
            "missing by key": await call(app, "GET", log(MISSING), keys.limited),
        }
    tokens.assert_alike()
    for pair in (("uncovered", "missing"), ("uncovered by key", "missing by key")):
        differ = same_answer(shown[pair[0]], JOB[UNCOVERED], shown[pair[1]], MISSING)
        assert differ is None, f"{pair[0]}: the limited key's log of {UNCOVERED}'s job is a missing job's: {differ}"
    assert shown["missing"].status_code == 200 and shown["missing"].content == b"", shown["missing"].text
    assert MARK[UNCOVERED] in shown["everyone"].text, f"a key without a list reads the log: {shown['everyone'].text}"
    assert MARK[COVERED] in shown["own"].text, f"the limited key reads its own job's log: {shown['own'].text}"


async def _sse(app: FastAPI, url: str, during: Callable[[], Awaitable[None]], token: str | None = None) -> Any:
    """What a server-sent events stream sends a client that stays a moment, while ``during`` runs: the status, the
    headers and the body. The stream ends when the client leaves."""
    path, _, query = url.partition("?")
    headers = [(b"host", b"test")]
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": headers,
        "server": ("test", 80),
        "client": ("127.0.0.1", 1),
        "root_path": "",
    }
    left = asyncio.Event()
    asked = False
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal asked
        if not asked:
            asked = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await left.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    task = asyncio.create_task(app(scope, receive, send))
    await asyncio.sleep(0.2)
    await during()
    await asyncio.sleep(0.3)
    left.set()
    await asyncio.wait_for(task, 5)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], sorted(start["headers"]), body


async def _stop_sse_shutdown_watcher() -> None:
    """sse-starlette's shutdown watcher outlives the test's loop otherwise (see tests/unit/mcp/test_http_wire.py)."""
    watchers = [t for t in asyncio.all_tasks() if getattr(t.get_coro(), "__name__", "") == "_shutdown_watcher"]
    for task in watchers:
        task.cancel()
    await asyncio.gather(*watchers, return_exceptions=True)


async def test_the_events_of_another_repository_s_qa_run_stream_as_a_missing_run_s(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    try:
        async with job_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
            tokens = _Tokens(app, "/api/v1/qa/runs/{run_id}/events-token")

            async def stream(name: str, run: str, key: str, *, by_token: bool = True) -> Any:
                async def publish() -> None:
                    if run != MISSING:
                        await app.state.qa_event_bus.publish(run, {"type": "app_status", "detail": f"live {run}"})

                if not by_token:
                    return await _sse(app, f"/api/v1/qa/runs/{run}/events", publish, token=key)
                token = await tokens.mint(name, run, key)
                return await _sse(app, f"/api/v1/qa/runs/{run}/events?token={token}", publish)

            shown = {
                "uncovered": await stream("uncovered", JOB[UNCOVERED], keys.limited),
                "missing": await stream("missing", MISSING, keys.limited),
                "missing to everyone": await stream("missing to everyone", MISSING, keys.open),
                "uncovered by key": await stream("uncovered by key", JOB[UNCOVERED], keys.limited, by_token=False),
                "everyone": await stream("everyone", JOB[UNCOVERED], keys.open),
                "own": await stream("own", JOB[COVERED], keys.limited),
            }
    finally:
        await _stop_sse_shutdown_watcher()
    tokens.assert_alike()
    quiet = {name: shown[name] for name in ("uncovered", "missing", "missing to everyone", "uncovered by key")}
    assert len({repr(answer) for answer in quiet.values()}) == 1 and shown["missing"][2] == b"", (
        f"the limited key's stream of {UNCOVERED}'s run is a missing run's, which sends nothing: {quiet}"
    )
    assert f"live {JOB[UNCOVERED]}".encode() in shown["everyone"][2], f"a key without a list streams the run: {shown}"
    assert f"live {JOB[COVERED]}".encode() in shown["own"][2], f"the limited key streams its own run: {shown['own']}"
