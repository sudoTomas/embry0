"""GET /v2/runs/{run_id}/apps/{app} route tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from athanor.storage.repositories.qa_app_results import QAAppResultRow
from athanor.workflows.qa.subtask_result_schema import CacheHits, SubTaskStatus

_KEY = "test-api-key-32-characters-minimum-x"


def _make(qa_repo):
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
    return TestClient(app)


def test_get_run_app_returns_app_result():
    qa_repo = AsyncMock()
    qa_repo.list_for_job = AsyncMock(
        return_value=[
            QAAppResultRow(
                job_id="j-1",
                app_name="companion",
                status=SubTaskStatus.QA_FAILURE,
                duration_ms=2000,
                cache_hits=CacheHits(),
                trace_url="https://x/trace.zip",
                failure_summary="loads page did not render",
                raw_result={"phase_reached": "exploratory"},
            ),
        ]
    )
    client = _make(qa_repo)
    resp = client.get(
        "/v2/runs/j-1/apps/companion",
        headers={"Authorization": f"Bearer {_KEY}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["app_name"] == "companion"
    assert body["status"] == "qa_failure"
    assert body["trace_url"] == "https://x/trace.zip"


def test_get_run_app_404_on_unknown_app():
    qa_repo = AsyncMock()
    qa_repo.list_for_job = AsyncMock(
        return_value=[
            QAAppResultRow(
                job_id="j-1",
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
    client = _make(qa_repo)
    resp = client.get(
        "/v2/runs/j-1/apps/notfound",
        headers={"Authorization": f"Bearer {_KEY}"},
    )
    assert resp.status_code == 404
