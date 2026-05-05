"""GET /v2/runs/{run_id} route tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from athanor.storage.repositories.qa_app_results import QAAppResultRow
from athanor.workflows.qa.subtask_result_schema import CacheHits, SubTaskStatus

_KEY = "test-api-key-32-characters-minimum-x"


def _make(qa_repo, jobs_repo):
    from athanor.api.app import create_app

    app = create_app()
    class _Cfg:
        pass
    cfg = _Cfg()
    cfg.api_key = _KEY
    cfg.auth_dev_mode = False
    cfg.webhook_dev_mode = False
    app.state.config = cfg
    app.state.qa_app_results_repo = qa_repo
    app.state.jobs_repo = jobs_repo
    return TestClient(app)


def test_get_run_detail_returns_apps_inlined():
    qa_repo = AsyncMock()
    qa_repo.list_for_job = AsyncMock(
        return_value=[
            QAAppResultRow(
                job_id="job-1",
                app_name="hub",
                status=SubTaskStatus.PASSED,
                duration_ms=1000,
                cache_hits=CacheHits(),
                trace_url=None,
                failure_summary=None,
                raw_result={},
            ),
            QAAppResultRow(
                job_id="job-1",
                app_name="companion",
                status=SubTaskStatus.QA_FAILURE,
                duration_ms=2000,
                cache_hits=CacheHits(prebaked_image=True),
                trace_url="https://x/trace.zip",
                failure_summary="loads page did not render",
                raw_result={},
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
    client = _make(qa_repo, jobs_repo)

    resp = client.get(
        "/v2/runs/job-1",
        headers={"Authorization": f"Bearer {_KEY}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == "job-1"
    assert body["repo"] == "org/r1"
    assert body["overall_status"] == "failed"  # companion failed
    assert len(body["apps"]) == 2
    by_app = {a["app_name"]: a for a in body["apps"]}
    assert by_app["companion"]["status"] == "qa_failure"
    assert by_app["companion"]["trace_url"] == "https://x/trace.zip"
    assert by_app["hub"]["cache_hits"]["prebaked_image"] is False
    assert by_app["companion"]["cache_hits"]["prebaked_image"] is True


def test_get_run_detail_404_on_unknown_job():
    qa_repo = AsyncMock()
    qa_repo.list_for_job = AsyncMock(return_value=[])
    jobs_repo = AsyncMock()
    jobs_repo.get = AsyncMock(return_value=None)
    client = _make(qa_repo, jobs_repo)
    resp = client.get(
        "/v2/runs/no-such-job",
        headers={"Authorization": f"Bearer {_KEY}"},
    )
    assert resp.status_code == 404


def test_get_run_detail_requires_auth():
    qa_repo = AsyncMock()
    jobs_repo = AsyncMock()
    client = _make(qa_repo, jobs_repo)
    resp = client.get("/v2/runs/job-1")
    assert resp.status_code == 401


def test_get_run_detail_overall_status_passed_when_all_pass():
    qa_repo = AsyncMock()
    qa_repo.list_for_job = AsyncMock(
        return_value=[
            QAAppResultRow(
                job_id="j",
                app_name="hub",
                status=SubTaskStatus.PASSED,
                duration_ms=1,
                cache_hits=CacheHits(),
                trace_url=None,
                failure_summary=None,
                raw_result={},
            ),
        ]
    )
    jobs_repo = AsyncMock()
    jobs_repo.get = AsyncMock(
        return_value={"job_id": "j", "repo": "org/r1", "started_at": datetime.now(UTC)}
    )
    client = _make(qa_repo, jobs_repo)
    resp = client.get("/v2/runs/j", headers={"Authorization": f"Bearer {_KEY}"})
    assert resp.json()["overall_status"] == "passed"
