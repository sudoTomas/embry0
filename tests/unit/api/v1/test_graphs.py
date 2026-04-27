from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from athanor.api.app import create_app
from athanor.config import AthanorConfig


@pytest.fixture
def app():
    config = AthanorConfig(_env_file=None, dev_mode=True)
    app = create_app(config)
    mock_registry = MagicMock()
    mock_workflow = MagicMock()
    mock_workflow.name = "issue-to-pr"
    mock_workflow.description = "Test workflow"
    mock_registry.get.return_value = mock_workflow
    mock_registry.list.return_value = [{"name": "issue-to-pr", "description": "Test"}]
    app.state.workflow_registry = mock_registry
    return app


@pytest.mark.asyncio
async def test_list_workflows(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/graphs/workflows")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.asyncio
async def test_execute_workflow_not_found(app):
    app.state.workflow_registry.get.return_value = None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/graphs/execute",
            json={"workflow": "nonexistent", "input_state": {}},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 404
