from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from legion.api.app import create_app
from legion.config import LegionConfig


@pytest.fixture
def app():
    config = LegionConfig(_env_file=None, dev_mode=True)
    app = create_app(config)
    mock_jobs = MagicMock()
    mock_jobs.create = AsyncMock(return_value="job-test123")
    mock_jobs.get = AsyncMock(
        return_value={
            "job_id": "job-test123",
            "status": "pending",
            "repo": "owner/repo",
            "task": "Fix bug",
            "total_cost_usd": 0.0,
            "budget_overrun_usd": 0.0,
        }
    )
    mock_jobs.list = AsyncMock(
        return_value=(
            [
                {
                    "job_id": "job-test123",
                    "status": "pending",
                    "repo": "owner/repo",
                    "task": "Fix bug",
                    "total_cost_usd": 0.0,
                    "budget_overrun_usd": 0.0,
                }
            ],
            1,
        )
    )
    mock_jobs.update = AsyncMock()
    app.state.jobs_repo = mock_jobs
    return app


@pytest.mark.asyncio
async def test_create_job(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/jobs",
            json={"repo": "owner/repo", "task": "Fix the bug"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 201
    assert resp.json()["job_id"] == "job-test123"


@pytest.mark.asyncio
async def test_list_jobs(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/jobs")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_get_job(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/jobs/job-test123")
    assert resp.status_code == 200
    assert resp.json()["job_id"] == "job-test123"


@pytest.mark.asyncio
async def test_get_job_not_found(app):
    app.state.jobs_repo.get = AsyncMock(return_value=None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/jobs/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cancel_job(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/jobs/job-test123/cancel", headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_create_job_invalid_repo(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/jobs",
            json={"repo": "invalid", "task": "Fix"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 422
