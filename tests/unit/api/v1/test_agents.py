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
