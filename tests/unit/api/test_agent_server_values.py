"""Who reads an agent's MCP server values as stored, and how a write sends a masked one back.

On the real app over a real database (:mod:`tests.fixtures.repository_writes`). An admin without a repository
allowlist (the master key, an admin key without a list) reads the values as stored and is the only caller that
changes an agent; every other caller (an operate or read key, an admin key with a list) reads them masked
(``embry0.api.agent_server_values``), in ``GET /agents`` and ``GET /agents/{type}`` alike.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from embry0.api.deps import bust_api_key_cache
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from tests.fixtures.repository_writes import MASTER, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres

SERVERS: dict[str, Any] = {
    "tracker": {
        "type": "stdio",
        "command": "tracker-mcp",
        "args": ["--api-key", "value-000000000011"],
        "env": {"TRACKER_TOKEN": "value-000000000012", "LOG_LEVEL": "debug"},
    },
    "remote": {
        "type": "http",
        "url": "https://mcp.example.test/v1?api_key=value-000000000013",
        "headers": {"Authorization": "Bearer value-000000000014"},
    },
}
SECRETS = [f"value-0000000000{n}" for n in range(11, 15)]


async def _keys(db: DatabasePool) -> dict[str, str]:
    repo, out = ApiKeysRepository(db), {}
    for scope in ("operate", "read"):
        _row, out[scope] = await repo.create(name=f"{scope}-open", scope=scope, user_id=None, created_by="tests")
    bust_api_key_cache()
    return out


async def _reads(app: Any, token: str) -> list[dict[str, Any]]:
    """What ``token`` reads of the developer's servers, in the list and alone."""
    listed = (await call(app, "GET", "/api/v1/agents", token)).json()
    one = (await call(app, "GET", "/api/v1/agents/developer", token)).json()
    return [next(a for a in listed if a["type"] == "developer")["mcp_servers"], one["mcp_servers"]]


async def test_only_an_admin_without_an_allowlist_reads_the_server_values_as_stored(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        stored = await call(app, "PUT", "/api/v1/agents/developer", MASTER, {"mcp_servers": SERVERS})
        assert stored.status_code == 200, stored.text
        assert stored.json()["mcp_servers"] == SERVERS, "the writer reads back what it wrote"
        more = await _keys(db_with_migrations)
        callers = {"master": MASTER, "open": keys.open, "limited": keys.limited, **more}
        reads = {name: await _reads(app, token) for name, token in callers.items()}
    assert reads["master"] == [SERVERS, SERVERS] and reads["open"] == [SERVERS, SERVERS], (
        f"an admin without an allowlist reads the values as stored: {reads['open']}"
    )
    for name in ("limited", "operate", "read"):
        text = json.dumps(reads[name])
        assert not [s for s in SECRETS if s in text], f"{name} reads a credential of the servers: {text}"
        assert reads[name][0] == reads[name][1], f"{name} reads the list and the agent alike: {reads[name]}"
        assert reads[name][0]["tracker"]["env"] == {"TRACKER_TOKEN": "****", "LOG_LEVEL": "****"}, reads[name]
        assert reads[name][0]["remote"]["headers"] == {"Authorization": "****"}, reads[name]


async def test_a_writer_sends_the_masked_view_back_unchanged(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The edit form saves what it was shown: a value masked and sent back as shown keeps the stored one, an edited
    value is stored, and a mask with no stored value behind it is a 422 naming where."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, _keys_, _values):
        await call(app, "PUT", "/api/v1/agents/developer", MASTER, {"mcp_servers": SERVERS})
        more = await _keys(db)
        shown = (await call(app, "GET", "/api/v1/agents/developer", more["operate"])).json()["mcp_servers"]
        shown["remote"]["headers"]["Authorization"] = "Bearer value-000000000015"
        saved = await call(app, "PUT", "/api/v1/agents/developer", MASTER, {"mcp_servers": shown})
        after = await db.fetchval("SELECT mcp_servers FROM agent_definitions WHERE type = 'developer'")
        fresh = {**shown, "fresh": {"type": "stdio", "command": "x", "env": {"TOKEN": "****"}}}
        refused = await call(app, "PUT", "/api/v1/agents/developer", MASTER, {"mcp_servers": fresh})
        created = await call(
            app,
            "POST",
            "/api/v1/agents",
            MASTER,
            {"type": "helper", "description": "Helps.", "model": "claude-sonnet-5", "mcp_servers": fresh},
        )
        edited = json.loads(json.dumps(shown))
        edited["remote"]["url"] = edited["remote"]["url"].replace("/v1?", "/v2?")
        half_edited = await call(app, "PUT", "/api/v1/agents/developer", MASTER, {"mcp_servers": edited})
    stored = json.loads(after) if isinstance(after, str) else after
    assert saved.status_code == 200, saved.text
    assert stored == {
        **SERVERS,
        "remote": {**SERVERS["remote"], "headers": {"Authorization": "Bearer value-000000000015"}},
    }, f"what is stored after the masked view is saved with one edit: {stored}"
    assert (refused.status_code, refused.json().get("detail")) == (
        422,
        "mcp_servers.fresh.env.TOKEN holds a masked value with no stored value behind it: send the value itself",
    ), f"a mask with no stored value behind it is refused: {refused.status_code} {refused.text[:200]}"
    assert created.status_code == 422 and "holds a masked value" in created.text, f"a new agent: {created.text}"
    assert (half_edited.status_code, half_edited.json().get("detail")) == (
        422,
        "mcp_servers.remote.url was edited from a masked value and still holds the mask: send the value itself",
    ), f"a masked URL edited around its mask would store the mask: {half_edited.status_code} {half_edited.text[:200]}"


async def test_a_job_s_agent_is_given_the_servers_as_stored(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The masked view is the readers': a job loads the agent's definition as stored, credentials included."""
    from embry0.orchestration.nodes.agent import load_agent_definition_or_none

    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, _keys, _values):
        await call(app, "PUT", "/api/v1/agents/developer", MASTER, {"mcp_servers": SERVERS})
        loaded = await load_agent_definition_or_none({"configurable": {"db": db}}, "developer")
    assert loaded is not None and loaded["mcp_servers"] == SERVERS, f"what a job's agent is given: {loaded}"
