from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from athanor.api.app import create_app
from athanor.config import AthanorConfig

SAMPLE_GRAPH = {"graph_id": "g1", "name": "Test", "nodes": [], "edges": []}

SAMPLE_TEMPLATE = {
    "id": "tmpl-1",
    "name": "My Template",
    "graph_definition": SAMPLE_GRAPH,
    "agent_models": {},
    "sandbox_profile": None,
}


@pytest.fixture
def app():
    config = AthanorConfig(_env_file=None, dev_mode=True)
    app = create_app(config)
    mock_repo = MagicMock()
    mock_repo.list_all = AsyncMock(return_value=[SAMPLE_TEMPLATE])
    mock_repo.get = AsyncMock(return_value=SAMPLE_TEMPLATE)
    mock_repo.create = AsyncMock(return_value=SAMPLE_TEMPLATE)
    mock_repo.update = AsyncMock(return_value=SAMPLE_TEMPLATE)
    mock_repo.delete = AsyncMock()
    mock_repo.duplicate = AsyncMock(return_value={**SAMPLE_TEMPLATE, "id": "tmpl-2", "name": "Copy of My Template"})
    app.state.templates_repo = mock_repo
    return app


@pytest.mark.asyncio
async def test_list_templates(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/pipelines/templates")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "tmpl-1"


@pytest.mark.asyncio
async def test_get_template(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/pipelines/templates/tmpl-1")
    assert resp.status_code == 200
    assert resp.json()["id"] == "tmpl-1"
    assert resp.json()["name"] == "My Template"


@pytest.mark.asyncio
async def test_get_template_not_found(app):
    app.state.templates_repo.get = AsyncMock(return_value=None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/pipelines/templates/nonexistent")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Template not found"


@pytest.mark.asyncio
async def test_create_template(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/pipelines/templates",
            json={"name": "My Template", "graph_definition": SAMPLE_GRAPH},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 201
    assert resp.json()["id"] == "tmpl-1"


@pytest.mark.asyncio
async def test_update_template(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/pipelines/templates/tmpl-1",
            json={"name": "Updated Template"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    assert resp.json()["id"] == "tmpl-1"


@pytest.mark.asyncio
async def test_delete_template(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete(
            "/api/v1/pipelines/templates/tmpl-1",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"id": "tmpl-1", "status": "deleted"}


@pytest.mark.asyncio
async def test_duplicate_template(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/pipelines/templates/tmpl-1/duplicate",
            json={"name": "Copy of My Template"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 201
    assert resp.json()["id"] == "tmpl-2"
    assert resp.json()["name"] == "Copy of My Template"
