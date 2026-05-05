"""End-to-end integration test for the /v2/... dashboard surface.

Boots a FastAPI TestClient with stubbed repos; walks the same endpoints the
dashboard's server-side fetches use. Exercises both Bearer and cookie auth.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from athanor.storage.repositories.qa_app_results import (
    AppHistoryRow,
    QAAppResultRow,
    RepoSummary,
    RunSummary,
)
from athanor.workflows.qa.subtask_result_schema import CacheHits, SubTaskStatus

_KEY = "phase-4-test-key-32chars-or-longer-x"


def _make_app(qa_repo, jobs_repo):
    from athanor.api.app import create_app

    app = create_app()
    class _Cfg:
        pass
    cfg = _Cfg()
    cfg.api_key = _KEY
    cfg.auth_dev_mode = False
    cfg.webhook_dev_mode = False
    cfg.dashboard_cookie_secure = False
    app.state.config = cfg
    app.state.qa_app_results_repo = qa_repo
    app.state.jobs_repo = jobs_repo
    return app, TestClient(app)


@pytest.fixture
def fixtures():
    qa_repo = AsyncMock()
    qa_repo.list_repos_with_runs = AsyncMock(
        return_value=[
            RepoSummary(
                repo="org/r1",
                latest_run_id="job-1",
                latest_status="failed",
                latest_started_at=datetime.now(UTC),
                latest_app_count=2,
            ),
        ]
    )
    qa_repo.list_runs_for_repo = AsyncMock(
        return_value=[
            RunSummary(
                job_id="job-1",
                repo="org/r1",
                started_at=datetime.now(UTC),
                overall_status="failed",
                app_count=2,
            ),
        ]
    )
    qa_repo.list_for_job = AsyncMock(
        return_value=[
            QAAppResultRow(
                job_id="job-1",
                app_name="hub",
                status=SubTaskStatus.PASSED,
                duration_ms=1100,
                cache_hits=CacheHits(),
                trace_url=None,
                failure_summary=None,
                raw_result={},
            ),
            QAAppResultRow(
                job_id="job-1",
                app_name="companion",
                status=SubTaskStatus.QA_FAILURE,
                duration_ms=2300,
                cache_hits=CacheHits(prebaked_image=True),
                trace_url="https://x/trace.zip",
                failure_summary="loads page did not render",
                raw_result={},
            ),
        ]
    )
    qa_repo.list_history_for_app = AsyncMock(
        return_value=[
            AppHistoryRow(
                job_id="job-1",
                app_name="hub",
                status="passed",
                duration_ms=1100,
                started_at=datetime.now(UTC),
                failure_summary=None,
            ),
        ]
    )

    jobs_repo = AsyncMock()
    jobs_repo.get = AsyncMock(
        return_value={
            "job_id": "job-1",
            "repo": "org/r1",
            "started_at": datetime.now(UTC),
        }
    )
    return qa_repo, jobs_repo


def test_full_dashboard_journey_with_bearer(fixtures):
    qa_repo, jobs_repo = fixtures
    app, client = _make_app(qa_repo, jobs_repo)
    headers = {"Authorization": f"Bearer {_KEY}"}

    # 1. Repo list
    r = client.get("/v2/repos", headers=headers)
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["repo"] == "org/r1"

    # 2. Repo's run list
    r = client.get("/v2/repos/org%2Fr1/runs", headers=headers)
    assert r.status_code == 200
    assert r.json()[0]["job_id"] == "job-1"

    # 3. Run detail (apps inlined)
    r = client.get("/v2/runs/job-1", headers=headers)
    assert r.status_code == 200
    detail = r.json()
    assert detail["overall_status"] == "failed"
    by_app = {a["app_name"]: a for a in detail["apps"]}
    assert by_app["companion"]["trace_url"] == "https://x/trace.zip"

    # 4. Per-run, per-app
    r = client.get("/v2/runs/job-1/apps/companion", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "qa_failure"

    # 5. App history
    r = client.get("/v2/repos/org%2Fr1/apps/hub/history", headers=headers)
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_full_dashboard_journey_with_cookie(fixtures):
    qa_repo, jobs_repo = fixtures
    app, client = _make_app(qa_repo, jobs_repo)

    # Login first
    r = client.post(
        "/v2/auth/dashboard/login",
        json={"api_key": _KEY},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 200
    assert "dashboard_session" in r.cookies

    # All other endpoints work with the cookie alone (TestClient persists it)
    r = client.get("/v2/repos")
    assert r.status_code == 200

    r = client.get("/v2/runs/job-1")
    assert r.status_code == 200


def test_unauth_request_is_rejected_consistently(fixtures):
    qa_repo, jobs_repo = fixtures
    app, client = _make_app(qa_repo, jobs_repo)
    for path in (
        "/v2/repos",
        "/v2/repos/org%2Fr1/runs",
        "/v2/runs/job-1",
        "/v2/runs/job-1/apps/companion",
        "/v2/repos/org%2Fr1/apps/hub/history",
    ):
        r = client.get(path)
        assert r.status_code == 401, f"{path} should require auth"


def test_openapi_schema_includes_all_v2_routes(fixtures):
    qa_repo, jobs_repo = fixtures
    app, client = _make_app(qa_repo, jobs_repo)

    r = client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    paths = spec["paths"]
    # Phase-4 endpoints registered:
    assert "/v2/auth/dashboard/login" in paths
    assert "/v2/repos" in paths
    assert "/v2/repos/{repo}/runs" in paths
    assert "/v2/runs/{run_id}" in paths
    assert "/v2/runs/{run_id}/apps/{app}" in paths
    assert "/v2/repos/{repo}/apps/{app}/history" in paths
