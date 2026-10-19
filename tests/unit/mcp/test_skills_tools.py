"""MCP parity for skills, skill sources, inline skills, per-agent skills and the tool catalog (spec 3.4)."""

import json

import pytest

from embry0.mcp_server import server
from embry0.mcp_server.client import Embry0Client
from tests.unit.mcp.test_client import capture, make_client
from tests.unit.mcp.test_server import RecordingClient

_WRITES = {
    "skill_source_add": lambda **kw: server.skill_source_add("mine", "git", url="https://example.com/a.git", **kw),
    "skill_source_sync": lambda **kw: server.skill_source_sync("mine", **kw),
    "skill_source_remove": lambda **kw: server.skill_source_remove("mine", **kw),
    "skill_source_update": lambda **kw: server.skill_source_update("mine", ref="v2", **kw),
    "inline_skill_put": lambda **kw: server.inline_skill_put("tidy", "Tidy up", "Do it.", **kw),
    "inline_skill_remove": lambda **kw: server.inline_skill_remove("tidy", **kw),
    "agent_skill_add": lambda **kw: server.agent_skill_add("developer", "superpowers:writing-plans", **kw),
    "agent_skill_remove": lambda **kw: server.agent_skill_remove("developer", "superpowers:writing-plans", **kw),
}


@pytest.fixture
def fake_client(monkeypatch):
    client = RecordingClient()
    monkeypatch.setattr(server, "_client", client)
    return client


@pytest.mark.parametrize("tool", sorted(_WRITES))
async def test_write_tools_refuse_without_confirm(fake_client, tool):
    refusal = await _WRITES[tool]()
    assert refusal["error"] == "confirmation_required", tool
    assert tool in refusal["message"] and "confirm=true" in refusal["message"]
    assert fake_client.calls == []


async def test_skill_source_add_forwards(fake_client):
    await server.skill_source_add("mine", "git", url="https://example.com/a.git", ref="v1", confirm=True)
    assert fake_client.calls == [
        (
            "skill_source_add",
            ("mine",),
            {"kind": "git", "url": "https://example.com/a.git", "ref": "v1", "enabled": True},
        )
    ]


async def test_write_tools_forward_with_confirm(fake_client):
    await server.skill_source_sync("mine", confirm=True)
    await server.skill_source_remove("mine", confirm=True)
    await server.inline_skill_put("tidy", "Tidy up", "Do it.", confirm=True)
    await server.inline_skill_remove("tidy", confirm=True)
    await server.agent_skill_add("developer", "superpowers:writing-plans", confirm=True)
    await server.agent_skill_remove("developer", "superpowers:writing-plans", confirm=True)
    assert fake_client.calls == [
        ("skill_source_sync", ("mine",), {}),
        ("skill_source_remove", ("mine",), {}),
        ("inline_skill_put", ("tidy",), {"description": "Tidy up", "body": "Do it."}),
        ("inline_skill_remove", ("tidy",), {}),
        ("agent_skill_add", ("developer", "superpowers:writing-plans"), {}),
        ("agent_skill_remove", ("developer", "superpowers:writing-plans"), {}),
    ]


async def test_read_tools_need_no_confirm(fake_client):
    assert await server.skill_list() == {"called": "skill_list"}
    assert await server.skill_source_list() == {"called": "skill_source_list"}
    assert await server.tool_list() == {"called": "tool_list"}
    assert await server.skill_source_get("mine") == {"called": "skill_source_get"}
    assert await server.inline_skill_list() == {"called": "inline_skill_list"}
    assert fake_client.calls == [
        ("skill_list", (), {}),
        ("skill_source_list", (), {}),
        ("tool_list", (), {}),
        ("skill_source_get", ("mine",), {}),
        ("inline_skill_list", (), {}),
    ]


async def test_skill_source_update_forwards_only_what_it_was_given(fake_client):
    await server.skill_source_update("mine", ref="v2", confirm=True)
    await server.skill_source_update("mine", enabled=False, confirm=True)
    assert fake_client.calls == [
        ("skill_source_update", ("mine",), {"ref": "v2", "enabled": None}),
        ("skill_source_update", ("mine",), {"ref": None, "enabled": False}),
    ]


async def test_client_skill_source_update_patches_only_the_given_fields():
    client, handler = _client_for({("PATCH", "/api/v1/skills/sources/mine"): {"id": "mine"}})
    await client.skill_source_update("mine", ref="v2")
    await client.skill_source_update("mine", enabled=False)
    assert [json.loads(r.content) for r in handler.seen] == [{"ref": "v2"}, {"enabled": False}]


async def test_client_new_reads_hit_their_routes():
    client, handler = _client_for(
        {
            ("GET", "/api/v1/skills/sources/mine"): {"id": "mine"},
            ("GET", "/api/v1/skills/inline"): [{"name": "tidy"}],
        }
    )
    assert await client.skill_source_get("mine") == {"id": "mine"}
    assert await client.inline_skill_list() == [{"name": "tidy"}]


async def test_client_percent_encodes_skill_names_and_source_ids():
    """A ``plugin:skill`` name, or any id with a path character, reaches the route as one segment."""
    routes = {
        ("POST", "/api/v1/agents/developer/skills/superpowers:writing-plans"): {"type": "developer"},
        ("DELETE", "/api/v1/agents/developer/skills/a/b"): {"type": "developer"},
        ("POST", "/api/v1/skills/sources/x/y/sync"): (202, {"id": "x/y"}),
        ("PUT", "/api/v1/skills/inline/a?b"): {"name": "a?b"},
    }
    client, handler = _client_for(routes)
    await client.agent_skill_add("developer", "superpowers:writing-plans")
    await client.agent_skill_remove("developer", "a/b")
    await client.skill_source_sync("x/y")
    await client.inline_skill_put("a?b", description="d", body="b")
    assert [r.url.raw_path.decode() for r in handler.seen] == [
        "/api/v1/agents/developer/skills/superpowers%3Awriting-plans",
        "/api/v1/agents/developer/skills/a%2Fb",
        "/api/v1/skills/sources/x%2Fy/sync",
        "/api/v1/skills/inline/a%3Fb",
    ]


def _client_for(routes: dict) -> tuple[Embry0Client, object]:
    handler = capture(routes)
    return make_client(handler), handler


async def test_client_reads_hit_the_catalog_routes():
    client, handler = _client_for(
        {
            ("GET", "/api/v1/skills"): [{"name": "local:tidy"}],
            ("GET", "/api/v1/skills/sources"): [{"id": "local"}],
            ("GET", "/api/v1/tools"): [{"name": "Read", "source": "builtin"}],
        }
    )
    assert await client.skill_list() == [{"name": "local:tidy"}]
    assert await client.skill_source_list() == [{"id": "local"}]
    assert await client.tool_list() == [{"name": "Read", "source": "builtin"}]


async def test_client_skill_source_add_posts_the_source():
    client, handler = _client_for({("POST", "/api/v1/skills/sources"): (201, {"id": "mine", "status": "pending"})})
    assert await client.skill_source_add("mine", kind="git", url="https://example.com/a.git", ref="v1") == {
        "id": "mine",
        "status": "pending",
    }
    assert handler.seen[("POST", "/api/v1/skills/sources")] == {
        "id": "mine",
        "kind": "git",
        "url": "https://example.com/a.git",
        "ref": "v1",
        "enabled": True,
    }


async def test_client_writes_hit_their_routes():
    routes = {
        ("POST", "/api/v1/skills/sources/mine/sync"): (202, {"id": "mine", "status": "syncing"}),
        ("DELETE", "/api/v1/skills/sources/mine"): {"id": "mine", "status": "deleted"},
        ("PUT", "/api/v1/skills/inline/tidy"): {"name": "tidy"},
        ("DELETE", "/api/v1/skills/inline/tidy"): {"name": "tidy", "status": "deleted"},
        ("POST", "/api/v1/agents/developer/skills/superpowers:writing-plans"): {"type": "developer"},
        ("DELETE", "/api/v1/agents/developer/skills/superpowers:writing-plans"): {"type": "developer"},
    }
    client, handler = _client_for(routes)
    await client.skill_source_sync("mine")
    await client.skill_source_remove("mine")
    await client.inline_skill_put("tidy", description="Tidy up", body="Do it.")
    await client.inline_skill_remove("tidy")
    await client.agent_skill_add("developer", "superpowers:writing-plans")
    await client.agent_skill_remove("developer", "superpowers:writing-plans")
    assert [(r.method, r.url.path) for r in handler.seen] == list(routes)
    assert json.loads(handler.seen[2].content) == {"description": "Tidy up", "body": "Do it."}
