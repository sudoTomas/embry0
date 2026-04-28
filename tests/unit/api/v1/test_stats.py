from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from athanor.api.app import create_app
from athanor.config import AthanorConfig


@pytest.fixture
def app():
    config = AthanorConfig(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config)
    mock_db = MagicMock()
    mock_db.fetchval = AsyncMock(return_value=0)
    mock_db.fetchrow = AsyncMock(return_value={"total_jobs": 0, "completed": 0, "failed": 0, "total_cost": 0.0})
    mock_db.fetch = AsyncMock(return_value=[])
    app.state.db = mock_db
    mock_traces = MagicMock()
    mock_traces.list = AsyncMock(return_value=([], 0))
    app.state.traces_repo = mock_traces
    return app


@pytest.mark.asyncio
async def test_get_stats(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_jobs" in data
    assert "total_issues" in data
    assert "success_rate" in data
    assert "daily_cost_usd" in data
    assert "recent_issues" in data


@pytest.mark.asyncio
async def test_list_traces(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/traces")
    assert resp.status_code == 200
    assert "traces" in resp.json()


@pytest.mark.asyncio
async def test_get_queue(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/queue")
    assert resp.status_code == 200
    assert "depth" in resp.json()
