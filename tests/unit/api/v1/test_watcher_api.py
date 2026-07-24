"""Watcher API endpoints (RAV-657)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config


@pytest.fixture
def app():
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config)
    app.state.watcher_runs_repo = MagicMock(
        list_recent=AsyncMock(return_value=[{"id": "wr-1", "action": "skipped_quiet", "log_lines": 1}])
    )
    app.state.watcher_service = MagicMock(run_once=AsyncMock(return_value={"action": "no_issue", "log_lines": 9}))
    return app


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_list_runs(app):
    async with _client(app) as client:
        resp = await client.get("/api/v1/watcher/runs")
    assert resp.status_code == 200
    assert resp.json()[0]["action"] == "skipped_quiet"
    app.state.watcher_runs_repo.list_recent.assert_awaited_once_with(limit=50)


@pytest.mark.asyncio
async def test_manual_trigger(app):
    async with _client(app) as client:
        resp = await client.post("/api/v1/watcher/run", headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200
    assert resp.json()["action"] == "no_issue"
    app.state.watcher_service.run_once.assert_awaited_once()


@pytest.mark.asyncio
async def test_endpoints_503_when_uninitialized(app):
    app.state.watcher_service = None
    app.state.watcher_runs_repo = None
    async with _client(app) as client:
        assert (await client.get("/api/v1/watcher/runs")).status_code == 503
        assert (
            await client.post("/api/v1/watcher/run", headers={"X-Requested-With": "XMLHttpRequest"})
        ).status_code == 503
