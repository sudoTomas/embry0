"""GET /jobs/{id}/verification serves the job_verifications row (game UI spec 3.6)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config

pytestmark = pytest.mark.asyncio

ROW = {
    "job_id": "job-v1",
    "repo": "owner/repo",
    "mode": "on",
    "tier": "backend_only",
    "gate_verdict": "passed",
    "blocking": False,
    "browser_qa": "skipped",
    "human_merge_only": False,
    "base_sha": "a" * 40,
    "head_sha": "b" * 40,
    "report": {"schema_version": 1, "gate": {"verdict": "passed", "targets": []}},
    "published_at": None,
    "created_at": datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
    "updated_at": datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
}


@pytest.fixture
def app():
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    app.state.jobs_repo = MagicMock()
    app.state.job_verifications_repo = MagicMock()
    app.state.job_verifications_repo.get = AsyncMock(return_value=dict(ROW))
    return app


async def _get(app, job_id: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(f"/api/v1/jobs/{job_id}/verification")


async def test_the_row_is_served_with_its_report_as_an_object(app):
    resp = await _get(app, "job-v1")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["gate_verdict"] == "passed"
    assert body["report"]["gate"]["verdict"] == "passed", "report must be an object, not a JSON string"
    assert body["created_at"].startswith("2026-10-20T12:00"), body["created_at"]
    app.state.job_verifications_repo.get.assert_awaited_once_with("job-v1")


async def test_a_report_stored_as_text_is_decoded(app):
    app.state.job_verifications_repo.get = AsyncMock(return_value={**ROW, "report": '{"gate": {"verdict": "failed"}}'})
    body = (await _get(app, "job-v1")).json()
    assert body["report"] == {"gate": {"verdict": "failed"}}, body["report"]


async def test_no_row_is_404(app):
    app.state.job_verifications_repo.get = AsyncMock(return_value=None)
    resp = await _get(app, "job-none")
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "No verification for this job"


async def test_production_wires_the_repository_on_app_state():
    """The route reads app.state.job_verifications_repo; nothing set it before this plan
    (the executor builds its own instance). Pin that _init_app_state assigns it."""
    import inspect

    from embry0.api import app as app_module

    src = inspect.getsource(app_module)
    assert "app.state.job_verifications_repo = JobVerificationsRepository(db)" in src, (
        "app.py must assign app.state.job_verifications_repo next to agent_sessions_repo"
    )
