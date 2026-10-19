from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config


@pytest.fixture
def app():
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config)
    mock_repo = MagicMock()
    mock_repo.list_all = AsyncMock(
        return_value=[
            {
                "type": "developer",
                "description": "Writes and edits code",
                "model": "claude-opus-4-5",
                "tools": ["bash", "editor"],
                "skills": [],
                "system_prompt": "",
                "is_builtin": True,
            }
        ]
    )
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
    assert len(data) >= 1
    dev = next((a for a in data if a["type"] == "developer"), None)
    assert dev is not None
    assert "description" in dev
    assert "model" in dev


# State-changing requests need the CSRF header the dashboard sends.
_CSRF = {"X-Requested-With": "XMLHttpRequest"}


@pytest.mark.asyncio
async def test_create_agent_rejects_unknown_avatar(app):
    app.state.agent_defs_repo.create = AsyncMock()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=_CSRF) as client:
        resp = await client.post(
            "/api/v1/agents",
            json={"type": "lantern", "description": "d", "model": "claude-sonnet-5", "avatar": "wizard"},
        )
    assert resp.status_code == 422
    assert "avatar must be one of" in resp.text
    app.state.agent_defs_repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_agent_passes_identity_to_repository(app):
    app.state.agent_defs_repo.create = AsyncMock(return_value={"type": "lantern"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=_CSRF) as client:
        resp = await client.post(
            "/api/v1/agents",
            json={
                "type": "lantern",
                "description": "d",
                "model": "claude-sonnet-5",
                "display_name": "Lantern",
                "avatar": "bard",
                "color": "sky",
                "tagline": "Sings.",
            },
        )
    assert resp.status_code == 201
    kwargs = app.state.agent_defs_repo.create.await_args.kwargs
    assert (kwargs["display_name"], kwargs["avatar"], kwargs["color"], kwargs["tagline"]) == (
        "Lantern",
        "bard",
        "sky",
        "Sings.",
    )


@pytest.mark.asyncio
async def test_update_agent_rejects_long_tagline(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=_CSRF) as client:
        resp = await client.put("/api/v1/agents/developer", json={"tagline": "x" * 141})
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["display_name", "avatar", "color", "tagline"])
async def test_update_agent_refuses_null_identity_field(app, field):
    app.state.agent_defs_repo.update = AsyncMock()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=_CSRF) as client:
        resp = await client.put("/api/v1/agents/developer", json={field: None})
    assert resp.status_code == 422
    assert f"{field} cannot be null" in resp.text
    app.state.agent_defs_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_agent_forwards_identity_fields(app):
    app.state.agent_defs_repo.update = AsyncMock(return_value={"type": "developer"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=_CSRF) as client:
        resp = await client.put("/api/v1/agents/developer", json={"avatar": "rogue", "color": "rose"})
    assert resp.status_code == 200
    assert app.state.agent_defs_repo.update.await_args.kwargs == {"avatar": "rogue", "color": "rose"}
