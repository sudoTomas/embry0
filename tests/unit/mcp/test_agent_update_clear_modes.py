"""The MCP ``agent_update`` tool clears an agent's execution and auth mode (#387).

``PUT /agents/{type}`` clears a column on an explicit ``null``, which is how
the agent form's "inherit" option works. The MCP client leaves every ``None``
out of the body, so ``execution_mode=None`` sent nothing and the tool answered
success with the mode still set. ``clear_execution_mode`` and
``clear_auth_mode`` send the null. Here the tool runs against the real app over
a real database (:mod:`tests.fixtures.repository_writes`), so the stored row is
what is read back, not a fake's idea of it.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from embry0.mcp_server import server
from embry0.mcp_server.client import Embry0Client
from embry0.storage.database import DatabasePool
from tests.fixtures.repository_writes import MASTER, repository_writes_app
from tests.unit.mcp.test_server import RecordingClient

AGENT = "developer"


class _RecordingTransport(httpx.AsyncBaseTransport):
    """The app's transport, keeping each PUT's JSON body."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner
        self.bodies: list[Any] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            self.bodies.append(json.loads(request.content))
        return await self._inner.handle_async_request(request)


async def _update(**fields: Any) -> dict[str, Any]:
    try:
        return await server.agent_update(AGENT, confirm=True, **fields)
    except TypeError as exc:
        pytest.fail(f"agent_update cannot clear an agent's mode: {exc}")


async def _modes(db: DatabasePool) -> tuple[str | None, str | None]:
    row = await db.fetchrow("SELECT execution_mode, auth_mode FROM agent_definitions WHERE type = $1", AGENT)
    assert row is not None, f"no {AGENT} row"
    return row["execution_mode"], row["auth_mode"]


@pytest.mark.requires_postgres
async def test_the_tool_clears_each_mode_and_the_row_reads_back_null(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, _keys, _values):
        transport = _RecordingTransport(ASGITransport(app=app, raise_app_exceptions=False))
        monkeypatch.setattr(server, "_client", Embry0Client("http://test", api_key=MASTER, transport=transport))

        answer = await _update(execution_mode="cli", auth_mode="api_key")
        assert "error" not in answer, answer
        assert await _modes(db_with_migrations) == ("cli", "api_key"), "the modes are set to begin with"

        answer = await _update(clear_execution_mode=True)
        assert transport.bodies[-1] == {"execution_mode": None}, transport.bodies[-1]
        assert await _modes(db_with_migrations) == (None, "api_key"), (
            "clear_execution_mode stores NULL and leaves the auth mode alone"
        )
        assert answer["execution_mode"] is None, answer

        answer = await _update(clear_auth_mode=True)
        assert transport.bodies[-1] == {"auth_mode": None}, transport.bodies[-1]
        assert await _modes(db_with_migrations) == (None, None), "clear_auth_mode stores NULL"
        assert answer["auth_mode"] is None, answer

        await _update(execution_mode="sdk", auth_mode="oauth")
        await _update(clear_execution_mode=True, clear_auth_mode=True)
        assert transport.bodies[-1] == {"execution_mode": None, "auth_mode": None}, transport.bodies[-1]
        assert await _modes(db_with_migrations) == (None, None), "both clear in one call"

        await _update(execution_mode="sdk", auth_mode="oauth")
        await _update(description="Writes the change")
        assert transport.bodies[-1] == {"description": "Writes the change"}, (
            "an update that clears nothing sends no null"
        )
        assert await _modes(db_with_migrations) == ("sdk", "oauth"), "and leaves both modes as they were"


@pytest.mark.parametrize("field, value", [("execution_mode", "cli"), ("auth_mode", "oauth")])
async def test_a_mode_and_its_clearing_at_once_are_refused(
    field: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = RecordingClient()
    monkeypatch.setattr(server, "_client", fake)
    answer = await _update(**{field: value, f"clear_{field}": True})
    assert "error" in answer and fake.calls == [], (answer, fake.calls)
