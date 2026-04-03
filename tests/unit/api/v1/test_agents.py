import pytest
from httpx import ASGITransport, AsyncClient

from legion.api.app import create_app
from legion.config import LegionConfig


@pytest.fixture
def app():
    config = LegionConfig(_env_file=None, dev_mode=True)
    return create_app(config)


@pytest.mark.asyncio
async def test_list_agent_types(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agents/types")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 4  # triage, developer, validator, reviewer, output

    # Check structure
    dev = next((a for a in data if a["type"] == "developer"), None)
    assert dev is not None
    assert "description" in dev
    assert "default_model" in dev
    assert "default_tools" in dev
    assert "inputs" in dev
    assert "outputs" in dev
    assert "responsibilities" in dev
    assert "phase" in dev


@pytest.mark.asyncio
async def test_agent_types_include_skills(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agents/types")
    data = resp.json()
    dev = next((a for a in data if a["type"] == "developer"), None)
    assert "default_skills" in dev
