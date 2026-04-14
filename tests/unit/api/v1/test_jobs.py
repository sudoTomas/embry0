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

    # Mock issue_executor for cancel_job endpoint
    mock_executor = MagicMock()
    mock_executor.cancel_job = AsyncMock()
    app.state.issue_executor = mock_executor
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
async def test_create_job_accepts_agent_models_override(app):
    """JobCreateRequest.agent_models flows into pipeline_config.agent_models_override."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/jobs",
            json={
                "repo": "owner/repo",
                "task": "Fix the bug",
                "agent_models": {"developer": "claude-haiku-4-5", "review": "claude-sonnet-4-6"},
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 201
    # The repo.create should have been called with pipeline_config containing
    # the override under the agreed key.
    app.state.jobs_repo.create.assert_called_once()
    kwargs = app.state.jobs_repo.create.call_args.kwargs
    assert kwargs["pipeline_config"] == {
        "agent_models_override": {"developer": "claude-haiku-4-5", "review": "claude-sonnet-4-6"},
    }


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
