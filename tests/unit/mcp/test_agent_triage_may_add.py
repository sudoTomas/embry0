"""The MCP agent tools write ``triage_may_add_skills`` through the agents API, which checks it."""

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


async def test_agent_update_forwards_what_triage_may_add(fake_client: RecordingClient) -> None:
    await server.agent_update("developer", triage_may_add_skills=["superpowers:brainstorming"], confirm=True)
    name, args, kwargs = fake_client.calls[-1]
    assert (name, args) == ("agent_update", ("developer",))
    assert kwargs.get("triage_may_add_skills") == ["superpowers:brainstorming"], kwargs


async def test_the_client_sends_what_triage_may_add_on_update_and_create() -> None:
    routes = {
        ("PUT", "/api/v1/agents/developer"): {"type": "developer"},
        ("POST", "/api/v1/agents"): (201, {"type": "lantern"}),
    }
    handler = capture(routes)
    client = make_client(handler)
    await client.agent_update("developer", triage_may_add_skills=[])
    await client.agent_create("lantern", description="d", model="m", triage_may_add_skills=["sp:a"])
    sent_update = handler.seen[("PUT", "/api/v1/agents/developer")]
    sent_create = handler.seen[("POST", "/api/v1/agents")]
    assert sent_update.get("triage_may_add_skills") == [], f"an empty list must be sent, not dropped: {sent_update}"
    assert sent_create.get("triage_may_add_skills") == ["sp:a"], sent_create
