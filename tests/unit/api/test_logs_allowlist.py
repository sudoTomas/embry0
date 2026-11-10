"""The container logs are the deployment's: a key with a repository allowlist reads none of them.

``GET /logs/query`` and ``GET /logs/containers`` read Loki, which holds every
container's lines; a line cannot be told to belong to one repository. So every
route of the logs router refuses a key with an allowlist, before Loki is
asked, as a deployment-wide write is refused: a 403 ``repo_not_allowed``
naming ``(the deployment)``, logged and audited under ``embry0:repo-allowlist``,
and nothing the key reads back in ``GET /audit``. The MCP ``logs_query`` tool
reaches the route with the caller's key, so it is refused too. A key without a
list reads the logs as before.

On the real app over a real database (:mod:`tests.fixtures.repository_writes`),
with a stand-in Loki that records each request.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from embry0.api.deps import bust_api_key_cache
from embry0.api.repo_allowlist import DEPLOYMENT, REFUSAL_CODE
from embry0.api.v1 import logs as logs_module
from embry0.mcp_server import server
from embry0.mcp_server.client import Embry0APIError, Embry0Client
from embry0.orchestration.dispatch_actors import REPO_ALLOWLIST
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from tests.fixtures.repository_writes import COVERED, call, repository_writes_app
from tests.fixtures.routes import app_routes

pytestmark = pytest.mark.requires_postgres

LOKI = "http://loki.test:3100"
LINE = "order saved for another repository"


def log_routes(app: FastAPI) -> list[str]:
    """Every route the logs router serves, as a URL to read."""
    paths = sorted(
        r.path
        for r in app_routes(app)
        if r.is_api_route and getattr(getattr(r.route, "endpoint", None), "__module__", None) == logs_module.__name__
    )
    return [f"{path}?container=web" if path.endswith("/query") else path for path in paths]


@pytest.fixture
def loki(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """A stand-in Loki: every request it gets, by path."""
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path.endswith("/label/container_name/values"):
            return httpx.Response(200, json={"data": ["web-1"]})
        return httpx.Response(
            200, json={"data": {"result": [{"stream": {"container_name": "web-1"}, "values": [["1", LINE]]}]}}
        )

    class _Httpx:
        """httpx as the logs module reads it, its clients answered by the stand-in; every other client is real."""

        def __getattr__(self, name: str) -> Any:
            return getattr(httpx, name)

        @staticmethod
        def AsyncClient(*args: Any, **kwargs: Any) -> httpx.AsyncClient:  # noqa: N802
            return httpx.AsyncClient(*args, **{**kwargs, "transport": httpx.MockTransport(answer)})

    monkeypatch.setitem(logs_module._CONTAINERS_CACHE, "values", [])  # noqa: SLF001
    monkeypatch.setattr(logs_module, "httpx", _Httpx())
    yield asked


async def _refusals(db: DatabasePool) -> list[tuple[str, str]]:
    rows = await db.fetch(
        "SELECT actor, details->>'repo' AS repo FROM audit_log WHERE action = 'apikey.repo_refused' ORDER BY id"
    )
    return [(str(r["actor"]), str(r["repo"])) for r in rows]


async def test_every_log_route_refuses_a_key_with_an_allowlist_before_loki_is_asked(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch, loki: list[str]
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        monkeypatch.setattr(app.state.config, "watcher_loki_url", LOKI)
        _row, operate = await ApiKeysRepository(db).create(
            name="dispatch", scope="operate", user_id=None, created_by="tests", allowed_repos=[COVERED]
        )
        bust_api_key_cache()
        routes = log_routes(app)
        found: dict[str, list[str]] = {}
        for url in routes:
            for name, token in (("admin", keys.limited), ("operate", operate)):
                before, audited = list(loki), (await call(app, "GET", "/api/v1/audit?limit=200", keys.limited))
                refused = await call(app, "GET", url, token)
                problems = found.setdefault(f"{name} {url}", [])
                detail = str(refused.json().get("detail")) if refused.status_code < 500 else refused.text
                if refused.status_code != 403 or not detail.startswith(REFUSAL_CODE) or DEPLOYMENT not in detail:
                    problems.append(f"not refused as the deployment's: {refused.status_code} {refused.text[:200]}")
                if LINE in refused.text or "web-1" in refused.text:
                    problems.append("a log line or a container name came back")
                if loki != before:
                    problems.append(f"Loki was asked: {loki[len(before) :]}")
                again = await call(app, "GET", "/api/v1/audit?limit=200", keys.limited)
                if again.json()["total"] != audited.json()["total"]:
                    problems.append("the key reads back a row its refused read left")
            reached = await call(app, "GET", url, keys.open)
            if reached.status_code != 200 or ("web-1" not in reached.text):
                found.setdefault(f"open {url}", []).append(f"not read: {reached.status_code} {reached.text[:200]}")
        refusals = await _refusals(db)
    assert len(routes) >= 2, f"the walk found {routes}: it measures little"
    assert {route: problems for route, problems in found.items() if problems} == {}
    assert refusals == [(REPO_ALLOWLIST, DEPLOYMENT)] * (2 * len(routes)), f"each refusal is audited once: {refusals}"


async def test_the_mcp_logs_query_tool_is_refused_to_a_key_with_an_allowlist(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch, loki: list[str]
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        monkeypatch.setattr(app.state.config, "watcher_loki_url", LOKI)
        outcome: dict[str, Any] = {}
        for name, token in (("limited", keys.limited), ("open", keys.open)):
            client = Embry0Client("http://test", api_key=token, transport=ASGITransport(app=app))
            monkeypatch.setattr(server, "_client", client)
            try:
                outcome[name] = await server.logs_query(container="web")
            except Embry0APIError as exc:
                outcome[name] = (exc.status_code, str(exc.detail))
    limited = outcome["limited"]
    assert isinstance(limited, tuple) and limited[0] == 403 and DEPLOYMENT in limited[1], limited
    assert isinstance(outcome["open"], dict) and outcome["open"]["count"] == 1, outcome["open"]
    assert loki == ["/loki/api/v1/query_range"], f"Loki was asked for the limited key: {loki}"
