from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from athanor.api.app import create_app
from athanor.config import AthanorConfig


@pytest.fixture
def app():
    config = AthanorConfig(_env_file=None, dev_mode=True)
    app = create_app(config)
    mock_budget = MagicMock()
    mock_budget.get = AsyncMock(
        return_value={
            "max_budget_per_job_usd": 10.0,
            "daily_cap_usd": 100.0,
            "monthly_cap_usd": 500.0,
            "rate_limit_per_author_per_hour": 5,
            "overrun_mode": "soft",
        }
    )
    mock_budget.update = AsyncMock()
    app.state.budget_repo = mock_budget
    mock_context = MagicMock()
    mock_context.get_global = AsyncMock(return_value={"system_context": "global", "assistant_context": ""})
    mock_context.set_global = AsyncMock()
    mock_context.get_repo = AsyncMock(return_value=None)
    mock_context.set_repo = AsyncMock()
    app.state.context_repo = mock_context
    return app


@pytest.mark.asyncio
async def test_get_budget(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/config/budget")
    assert resp.status_code == 200
    assert resp.json()["max_budget_per_job_usd"] == 10.0


@pytest.mark.asyncio
async def test_update_budget(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/config/budget",
            json={"max_budget_per_job_usd": 25.0},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_global_context(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/config/context")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_set_global_context(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/config/context",
            json={"system_context": "Use TypeScript."},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
