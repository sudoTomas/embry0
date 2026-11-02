"""An agent definition can list only the tools embry0 gates: a 422 names the others."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config

_STORED = {
    "type": "custom",
    "description": "d",
    "model": "claude-sonnet-4-6",
    "tools": ["Read", "LegacyTool"],
    "skills": [],
    "system_prompt": "",
    "is_builtin": False,
    "mcp_servers": {"tracker": {"command": "npx", "args": ["tracker-mcp"]}},
}
_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture
def app():
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    repo = MagicMock()
    repo.get = AsyncMock(return_value=_STORED)
    repo.create = AsyncMock(return_value=_STORED)
    repo.update = AsyncMock(return_value=_STORED)
    app.state.agent_defs_repo = repo
    return app


async def _send(app, method: str, path: str, body: dict):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, json=body, headers=_HEADERS)


def _create(tools: list[str], **extra) -> dict:
    return {"type": "custom", "description": "d", "model": "claude-sonnet-4-6", "tools": tools, **extra}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["Monitor", "Workflow", "CronCreate", "mcp__elsewhere__run"])
async def test_creating_a_definition_with_a_tool_embry0_does_not_gate_is_a_422(app, tool):
    resp = await _send(app, "POST", "/api/v1/agents", _create(["Read", "Bash", tool]))
    assert resp.status_code == 422, f"POST with {tool!r}: {resp.status_code} {resp.text[:200]}"
    assert tool in resp.text, resp.text[:300]
    assert not app.state.agent_defs_repo.create.called, "the definition was written"


@pytest.mark.asyncio
async def test_creating_a_definition_with_catalog_tools_and_its_own_mcp_tools_succeeds(app):
    body = _create(
        ["Read", "Bash", "mcp__playwright__browser_navigate", "mcp__tracker__search"],
        mcp_servers={"tracker": {"command": "npx", "args": ["tracker-mcp"]}},
    )
    resp = await _send(app, "POST", "/api/v1/agents", body)
    assert resp.status_code == 201, resp.text[:300]


@pytest.mark.asyncio
async def test_updating_a_definition_to_add_a_tool_embry0_does_not_gate_is_a_422(app):
    resp = await _send(app, "PUT", "/api/v1/agents/custom", {"tools": ["Read", "Monitor"]})
    assert resp.status_code == 422, f"{resp.status_code} {resp.text[:200]}"
    assert "Monitor" in resp.text, resp.text[:300]
    assert not app.state.agent_defs_repo.update.called, "the definition was written"


@pytest.mark.asyncio
async def test_updating_a_definition_checks_the_tools_it_already_has(app):
    """A stored name outside the catalog is refused too: the definition saves once it is removed."""
    resp = await _send(app, "PUT", "/api/v1/agents/custom", {"tools": ["Read", "LegacyTool", "mcp__tracker__search"]})
    assert resp.status_code == 422 and "LegacyTool" in resp.text, f"{resp.status_code} {resp.text[:200]}"
    resp = await _send(app, "PUT", "/api/v1/agents/custom", {"description": "only the description"})
    assert resp.status_code == 200, "a write that does not touch tools or mcp_servers saves"
    resp = await _send(app, "PUT", "/api/v1/agents/custom", {"mcp_servers": {}})
    assert resp.status_code == 422 and "LegacyTool" in resp.text, "changing mcp_servers checks the stored tools"
    resp = await _send(app, "PUT", "/api/v1/agents/custom", {"tools": ["Read", "Task", "mcp__tracker__search"]})
    assert resp.status_code == 200, resp.text[:300]


@pytest.mark.asyncio
async def test_an_update_checks_mcp_tools_against_the_servers_it_sends(app):
    resp = await _send(
        app,
        "PUT",
        "/api/v1/agents/custom",
        {"tools": ["Read", "mcp__tracker__search"], "mcp_servers": {"other": {"command": "x"}}},
    )
    assert resp.status_code == 422, f"{resp.status_code} {resp.text[:200]}"
    assert "mcp__tracker__search" in resp.text
