"""GET /v2/repos/{repo}/runs route tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from athanor.storage.repositories.qa_app_results import RunSummary

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


def test_list_runs_for_repo_returns_pagination():
    qa_repo = AsyncMock()
    qa_repo.list_runs_for_repo = AsyncMock(
        return_value=[
            RunSummary(
                job_id=f"j-{i}",
                repo="org/r1",
                started_at=datetime.now(UTC),
                overall_status="passed",
                app_count=2,
            )
            for i in range(3)
        ]
    )
    client = _make(qa_repo)
    resp = client.get(
        "/v2/repos/org%2Fr1/runs?limit=3&offset=0",
        headers={"Authorization": f"Bearer {_KEY}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert body[0]["job_id"] == "j-0"
    qa_repo.list_runs_for_repo.assert_called_once_with("org/r1", limit=3, offset=0)


def test_list_runs_for_repo_default_pagination():
    qa_repo = AsyncMock()
    qa_repo.list_runs_for_repo = AsyncMock(return_value=[])
    client = _make(qa_repo)
    resp = client.get(
        "/v2/repos/org%2Fr2/runs",
        headers={"Authorization": f"Bearer {_KEY}"},
    )
    assert resp.status_code == 200
    qa_repo.list_runs_for_repo.assert_called_once_with("org/r2", limit=50, offset=0)


def test_list_runs_for_repo_requires_auth():
    qa_repo = AsyncMock()
    client = _make(qa_repo)
    resp = client.get("/v2/repos/org%2Fr1/runs")
    assert resp.status_code == 401
