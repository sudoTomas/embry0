from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config


@pytest.fixture
def app():
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
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
    mock_jobs.list_all = AsyncMock(
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
async def test_list_jobs_brief_cuts_each_task_to_its_first_line(app):
    """``?brief=true`` keeps a list page small: a watcher analysis job's task
    is its whole log briefing, and twenty of them weighed close to a megabyte
    per page load. The default stays the full text, so every other client
    sees what it saw before."""
    briefing = "Analyze the following production logs\n" + "x" * 50_000
    one_long_line = "y" * 300
    app.state.jobs_repo.list_all = AsyncMock(
        return_value=(
            [
                {"job_id": "job-briefing", "status": "completed", "repo": "owner/repo", "task": briefing},
                {"job_id": "job-long-line", "status": "completed", "repo": "owner/repo", "task": one_long_line},
                {"job_id": "job-short", "status": "pending", "repo": "owner/repo", "task": "Fix bug"},
            ],
            3,
        )
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        brief = await client.get("/api/v1/jobs", params={"brief": "true"})
        full = await client.get("/api/v1/jobs")
    assert brief.status_code == 200
    jobs = brief.json()["jobs"]
    assert jobs[0]["task"] == "Analyze the following production logs…"
    assert jobs[0]["task_truncated"] is True
    assert jobs[1]["task"] == "y" * 200 + "…"
    assert jobs[1]["task_truncated"] is True
    assert jobs[2]["task"] == "Fix bug"
    assert jobs[2]["task_truncated"] is False
    assert len(brief.content) < 2_000
    assert full.json()["jobs"][0]["task"] == briefing
    assert full.json()["jobs"][0]["task_truncated"] is False


@pytest.mark.asyncio
async def test_list_jobs_surfaces_issue_id_and_current_stage(app):
    """GET /jobs builds JobResponse from the row — the Console board fields
    (issue_id, current_stage) must survive the model, and legacy rows without
    them must serialize as null rather than fail validation."""
    app.state.jobs_repo.list_all = AsyncMock(
        return_value=(
            [
                {
                    "job_id": "job-issue1",
                    "status": "running",
                    "repo": "owner/repo",
                    "task": "Fix bug",
                    "issue_id": "iss-1",
                    "current_stage": "triage_complete",
                },
                {
                    # Legacy-shaped row: no issue_id/current_stage keys at all.
                    "job_id": "job-legacy",
                    "status": "completed",
                    "repo": "owner/repo",
                    "task": "Old job",
                },
            ],
            2,
        )
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/jobs")
    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    assert jobs[0]["issue_id"] == "iss-1"
    assert jobs[0]["current_stage"] == "triage_complete"
    assert jobs[1]["issue_id"] is None
    assert jobs[1]["current_stage"] is None


@pytest.mark.asyncio
async def test_list_jobs_tolerates_null_repo(app):
    """Non-git jobs (http/local/none contexts) persist repo=NULL — the list
    endpoint must serialize them instead of 500ing on JobResponse validation
    (RAV-604 regression: the first research job broke GET /jobs)."""
    app.state.jobs_repo.list_all = AsyncMock(
        return_value=(
            [
                {
                    "job_id": "job-research",
                    "status": "completed",
                    "repo": None,
                    "task": "Summarize RFC 2324",
                    "job_kind": "research",
                }
            ],
            1,
        )
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/jobs")
    assert resp.status_code == 200
    job = resp.json()["jobs"][0]
    assert job["repo"] is None
    assert job["job_kind"] == "research"


@pytest.mark.asyncio
async def test_get_job(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/jobs/job-test123")
    assert resp.status_code == 200
    assert resp.json()["job_id"] == "job-test123"


@pytest.mark.asyncio
async def test_get_job_serves_the_per_run_failure_record(app):
    """#281: job_get / the dashboard read why each errored run failed."""
    traces = AsyncMock()
    traces.sum_by_agent_for_job = AsyncMock(return_value=[])
    traces.failures_for_job = AsyncMock(
        return_value=[{"agent_type": "developer", "error_code": "ERR_AGENT_PROCESS_FAILED", "stderr_tail": "ENOSPC"}]
    )
    app.state.traces_repo = traces
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/jobs/job-test123")
    assert resp.status_code == 200
    assert resp.json()["agent_failures"][0]["stderr_tail"] == "ENOSPC"
    traces.failures_for_job.assert_awaited_once_with("job-test123")


@pytest.mark.asyncio
async def test_get_job_survives_a_failing_failure_query(app):
    traces = AsyncMock()
    traces.sum_by_agent_for_job = AsyncMock(return_value=[])
    traces.failures_for_job = AsyncMock(side_effect=RuntimeError("column does not exist"))
    app.state.traces_repo = traces
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/jobs/job-test123")
    assert resp.status_code == 200
    assert resp.json()["agent_failures"] == []


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


@pytest.fixture
def client_with_paused_job(app):
    """Client with a mock paused job and resume-capable executor."""
    paused_job = {
        "job_id": "job-paused123",
        "status": "paused",
        "repo": "owner/repo",
        "task": "Fix bug",
        "issue_id": "iss-12345678",
        "total_cost_usd": 0.0,
        "budget_overrun_usd": 0.0,
    }
    app.state.jobs_repo.get = AsyncMock(return_value=paused_job)

    # Mock executor with resume capability
    app.state.issue_executor.schedule_resume = MagicMock()

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return client, paused_job["job_id"]


@pytest.mark.asyncio
async def test_resume_job_rejects_invalid_choice(client_with_paused_job):
    """resume_job's choice field is enum-validated."""
    client, job_id = client_with_paused_job
    resp = await client.post(
        f"/api/v1/jobs/{job_id}/resume",
        json={"choice": "nope"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "choice" in str(body)


@pytest.mark.asyncio
async def test_resume_job_rejects_oversized_guidance(client_with_paused_job):
    """guidance > 50KB → 422."""
    client, job_id = client_with_paused_job
    resp = await client.post(
        f"/api/v1/jobs/{job_id}/resume",
        json={"choice": "continue_retrying", "guidance": "x" * 50001},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_resume_job_accepts_valid_choice(client_with_paused_job):
    """Happy path."""
    client, job_id = client_with_paused_job
    resp = await client.post(
        f"/api/v1/jobs/{job_id}/resume",
        json={"choice": "merge_as_is"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    assert resp.json()["choice"] == "merge_as_is"


@pytest.mark.asyncio
async def test_resume_job_without_issue(app):
    """Issue-less API-submitted jobs (issue_id None) must be resumable —
    regression for the 400 that made every paused API job unrecoverable."""
    paused_job = {
        "job_id": "job-noissue12",
        "status": "paused",
        "repo": "owner/repo",
        "task": "Fix bug",
        "issue_id": None,
        "total_cost_usd": 0.0,
        "budget_overrun_usd": 0.0,
    }
    app.state.jobs_repo.get = AsyncMock(return_value=paused_job)
    app.state.issue_executor.schedule_resume = MagicMock()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/jobs/job-noissue12/resume",
            json={"choice": "continue_retrying"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "resuming"
    # Executor invoked with issue_id=None, not rejected at the endpoint.
    app.state.issue_executor.schedule_resume.assert_called_once()
    assert app.state.issue_executor.schedule_resume.call_args.args[0] is None


@pytest.mark.asyncio
async def test_resume_job_rejects_extra_fields(client_with_paused_job):
    """extra='forbid' should reject unexpected fields."""
    client, job_id = client_with_paused_job
    resp = await client.post(
        f"/api/v1/jobs/{job_id}/resume",
        json={"choice": "continue_retrying", "extra_field": "boom"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_job_dispatches_workflow(app):
    """A direct (non-QA) job must DISPATCH the issue-to-pr workflow, not just
    create a pending row. Regression: jobs previously sat in `pending` forever
    because POST /jobs never kicked the executor (only the issues path did)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/jobs",
            json={"repo": "owner/repo", "task": "Fix the bug"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 201
    executor = app.state.issue_executor
    executor.submit_job.assert_called_once()
    args, kwargs = executor.submit_job.call_args
    assert args[0] == "job-test123"
    assert kwargs["repo"] == "owner/repo"
    assert kwargs["title"] == "Fix the bug"


# --- RAV-1297: agent_models overrides must name enabled models ---


@pytest.mark.asyncio
async def test_create_job_rejects_disabled_agent_model_override(app):
    provider_repo = MagicMock()
    provider_repo.get = AsyncMock(return_value={"disabled_models": ["grok-4.5"]})
    app.state.provider_repo = provider_repo
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/jobs",
            json={"repo": "owner/repo", "task": "t", "agent_models": {"research": "grok-4.5"}},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 422
    assert "disabled in provider settings" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_job_enabled_agent_model_override_passes(app):
    provider_repo = MagicMock()
    provider_repo.get = AsyncMock(return_value={"disabled_models": ["grok-4.5"]})
    app.state.provider_repo = provider_repo
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/jobs",
            json={"repo": "owner/repo", "task": "t", "agent_models": {"research": "claude-sonnet-5"}},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 201
