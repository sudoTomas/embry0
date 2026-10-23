from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config

DEVELOPER_AGENT = {
    "type": "developer",
    "description": "Writes and edits code",
    "model": "claude-opus-4-5",
    "tools": ["bash", "editor"],
    "skills": [],
    "system_prompt": "",
    "is_builtin": True,
}


@pytest.fixture
def app():
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config)
    mock_repo = MagicMock()
    mock_repo.list_all = AsyncMock(return_value=[DEVELOPER_AGENT])
    mock_repo.get = AsyncMock(return_value=DEVELOPER_AGENT)
    mock_repo.create = AsyncMock(return_value={**DEVELOPER_AGENT, "type": "custom", "is_builtin": False})
    mock_repo.update = AsyncMock(return_value={**DEVELOPER_AGENT, "description": "Updated"})
    mock_repo.delete = AsyncMock()
    mock_repo.reset = AsyncMock(return_value=DEVELOPER_AGENT)
    app.state.agent_defs_repo = mock_repo
    return app


@pytest.mark.asyncio
async def test_list_agents(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["type"] == "developer"


@pytest.mark.asyncio
async def test_get_agent(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agents/developer")
    assert resp.status_code == 200
    assert resp.json()["type"] == "developer"


@pytest.mark.asyncio
async def test_get_agent_not_found(app):
    app.state.agent_defs_repo.get = AsyncMock(return_value=None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agents/nonexistent")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Agent type not found"


@pytest.mark.asyncio
async def test_create_agent(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/agents",
            json={
                "type": "custom",
                "description": "A custom agent",
                "model": "claude-haiku-3-5",
                "tools": [],
                "skills": [],
                "system_prompt": "",
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 201
    data = resp.json()
    assert data["type"] == "custom"
    assert data["is_builtin"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("reserved", ["reviewer", "output", "qa"])
async def test_create_agent_refuses_a_fixed_node_type(app, reserved):
    """A row named after a fixed node would be placeable but never run on generic_agent."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/agents",
            json={"type": reserved, "description": "d", "model": "m"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 422, resp.text
    message = str(resp.json()["detail"])
    assert f"{reserved!r} is reserved" in message, message
    assert "'__start__'" in message and "'output'" in message, message
    app.state.agent_defs_repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_agent_full_surface(app):
    """RAV-604: execution_mode/auth_mode/mcp_servers reach the repo."""
    transport = ASGITransport(app=app)
    mcp = {"playwright": {"type": "stdio", "command": "playwright-mcp"}}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/agents",
            json={
                "type": "custom",
                "description": "A custom agent",
                "model": "claude-sonnet-4-6",
                "execution_mode": "sdk",
                "auth_mode": "oauth",
                "mcp_servers": mcp,
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 201
    kwargs = app.state.agent_defs_repo.create.call_args.kwargs
    assert kwargs["execution_mode"] == "sdk"
    assert kwargs["auth_mode"] == "oauth"
    assert kwargs["mcp_servers"] == mcp


@pytest.mark.asyncio
async def test_create_agent_rejects_bad_modes(app):
    transport = ASGITransport(app=app)
    base = {"type": "custom", "description": "d", "model": "m"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for bad in ({"execution_mode": "carrier-pigeon"}, {"auth_mode": "vibes"}):
            resp = await client.post(
                "/api/v1/agents",
                json={**base, **bad},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            assert resp.status_code == 422, bad


@pytest.mark.asyncio
async def test_update_agent_accepts_new_fields(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/agents/developer",
            json={"execution_mode": "cli", "mcp_servers": {"x": {"type": "stdio", "command": "x"}}},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    kwargs = app.state.agent_defs_repo.update.call_args.kwargs
    assert kwargs["execution_mode"] == "cli"
    assert kwargs["mcp_servers"] == {"x": {"type": "stdio", "command": "x"}}


@pytest.mark.asyncio
async def test_update_agent(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/agents/developer",
            json={"description": "Updated"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    assert resp.json()["description"] == "Updated"


@pytest.mark.asyncio
async def test_delete_agent(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete(
            "/api/v1/agents/developer",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "developer"
    assert data["status"] == "deleted"


@pytest.mark.asyncio
async def test_delete_builtin_agent_fails(app):
    app.state.agent_defs_repo.delete = AsyncMock(side_effect=ValueError("Cannot delete built-in agent"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete(
            "/api/v1/agents/developer",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 403
    assert "Cannot delete built-in agent" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_reset_agent(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/agents/developer/reset",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    assert resp.json()["type"] == "developer"


@pytest.mark.asyncio
async def test_reset_agent_not_found(app):
    app.state.agent_defs_repo.reset = AsyncMock(side_effect=ValueError("Agent type not found"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/agents/nonexistent/reset",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 404
    assert "Agent type not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_update_agent_explicit_null_clears_field(app):
    """RAV-1058: exclude_unset semantics — an explicitly-sent null CLEARS the
    column (the dashboard's "inherit default" for execution_mode/auth_mode),
    while omitted fields stay untouched."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/agents/developer",
            json={"execution_mode": None, "description": "d"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    kwargs = app.state.agent_defs_repo.update.call_args.kwargs
    assert kwargs["execution_mode"] is None  # explicit null → clears
    assert kwargs["description"] == "d"
    assert "auth_mode" not in kwargs  # omitted → untouched


# --- RAV-1297: save-time rejection of disabled models ---


@pytest.mark.asyncio
async def test_create_agent_rejects_disabled_model(app):
    provider_repo = MagicMock()
    provider_repo.get = AsyncMock(return_value={"disabled_models": ["grok-4.5"]})
    app.state.provider_repo = provider_repo
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/agents",
            json={"type": "custom", "description": "d", "model": "grok-4.5"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 422
    assert "disabled in provider settings" in resp.json()["detail"]
    app.state.agent_defs_repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_agent_rejects_disabled_model(app):
    provider_repo = MagicMock()
    provider_repo.get = AsyncMock(return_value={"disabled_models": ["claude-sonnet-4-6"]})
    app.state.provider_repo = provider_repo
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/agents/developer",
            json={"model": "claude-sonnet-4-6"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 422
    app.state.agent_defs_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_agent_enabled_model_passes_gate(app):
    provider_repo = MagicMock()
    provider_repo.get = AsyncMock(return_value={"disabled_models": ["grok-4.5"]})
    app.state.provider_repo = provider_repo
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/agents/developer",
            json={"model": "claude-opus-5"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
