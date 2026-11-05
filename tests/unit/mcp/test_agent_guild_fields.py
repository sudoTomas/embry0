"""The MCP agent tools write an agent's ``station`` and ``quest_template`` (#167) through the agents API."""

from __future__ import annotations

import pytest

from embry0.mcp_server import server
from tests.unit.mcp.test_client import capture, make_client
from tests.unit.mcp.test_server import RecordingClient


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> RecordingClient:
    client = RecordingClient()
    monkeypatch.setattr(server, "_client", client)
    return client


async def test_agent_update_forwards_the_station_and_the_task(fake_client: RecordingClient) -> None:
    await server.agent_update("scout", station="lectern", quest_template="scout-pass", confirm=True)
    name, args, kwargs = fake_client.calls[-1]
    assert (name, args) == ("agent_update", ("scout",))
    assert (kwargs.get("station"), kwargs.get("quest_template"), kwargs.get("clear_quest_template")) == (
        "lectern",
        "scout-pass",
        False,
    ), kwargs


async def test_agent_update_refuses_a_task_and_its_clearing_at_once(fake_client: RecordingClient) -> None:
    result = await server.agent_update("scout", quest_template="x", clear_quest_template=True, confirm=True)
    assert "error" in result and fake_client.calls == [], result


async def test_the_client_sends_the_task_its_clearing_and_the_station() -> None:
    routes = {
        ("PUT", "/api/v1/agents/scout"): {"type": "scout"},
        ("POST", "/api/v1/agents"): (201, {"type": "scout"}),
    }
    handler = capture(routes)
    client = make_client(handler)
    await client.agent_update("scout", quest_template="")
    assert handler.seen[("PUT", "/api/v1/agents/scout")] == {"quest_template": ""}, "'' names no template: it is sent"
    await client.agent_update("scout", clear_quest_template=True)
    assert handler.seen[("PUT", "/api/v1/agents/scout")] == {"quest_template": None}, (
        "clearing sends the one null that takes the task out of the guild"
    )
    await client.agent_update("scout", station="map_table")
    assert handler.seen[("PUT", "/api/v1/agents/scout")] == {"station": "map_table"}, "nothing else is cleared"
    await client.agent_create("scout", description="d", model="m", station="anvil", quest_template="why")
    sent = handler.seen[("POST", "/api/v1/agents")]
    assert (sent.get("station"), sent.get("quest_template")) == ("anvil", "why"), sent
