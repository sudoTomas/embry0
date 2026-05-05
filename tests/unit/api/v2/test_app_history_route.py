"""GET /v2/repos/{repo}/apps/{app}/history route tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from athanor.storage.repositories.qa_app_results import AppHistoryRow

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


def test_get_app_history_returns_rows():
    qa_repo = AsyncMock()
    qa_repo.list_history_for_app = AsyncMock(
        return_value=[
            AppHistoryRow(
                job_id=f"hist-{i}",
                app_name="hub",
                status="passed" if i != 1 else "qa_failure",
                duration_ms=1000 + i * 100,
                started_at=datetime.now(UTC),
                failure_summary=None if i != 1 else "companion broke",
            )
            for i in range(3)
        ]
    )
    client = _make(qa_repo)
    resp = client.get(
        "/v2/repos/org%2Fr1/apps/hub/history?limit=10",
        headers={"Authorization": f"Bearer {_KEY}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert body[1]["status"] == "qa_failure"
    qa_repo.list_history_for_app.assert_called_once_with("org/r1", "hub", limit=10)


def test_get_app_history_default_limit():
    qa_repo = AsyncMock()
    qa_repo.list_history_for_app = AsyncMock(return_value=[])
    client = _make(qa_repo)
    resp = client.get(
        "/v2/repos/org%2Fr1/apps/hub/history",
        headers={"Authorization": f"Bearer {_KEY}"},
    )
    assert resp.status_code == 200
    qa_repo.list_history_for_app.assert_called_once_with("org/r1", "hub", limit=20)


def test_get_app_history_requires_auth():
    qa_repo = AsyncMock()
    client = _make(qa_repo)
    resp = client.get("/v2/repos/org%2Fr1/apps/hub/history")
    assert resp.status_code == 401
