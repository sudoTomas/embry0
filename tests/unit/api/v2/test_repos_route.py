"""GET /v2/repos route tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from athanor.storage.repositories.qa_app_results import RepoSummary

_VALID_KEY = "test-api-key-32-characters-minimum-x"


def _make_app(qa_app_results_repo) -> tuple:
    from athanor.api.app import create_app

    app = create_app()
    class _Cfg:
        pass
    cfg = _Cfg()
    cfg.api_key = _VALID_KEY
    cfg.auth_dev_mode = False
    cfg.webhook_dev_mode = False
    app.state.config = cfg
    app.state.qa_app_results_repo = qa_app_results_repo
    return app, TestClient(app)


def test_get_repos_returns_list_with_auth():
    qa_repo = AsyncMock()
    qa_repo.list_repos_with_runs = AsyncMock(
        return_value=[
            RepoSummary(
                repo="org/r1",
                latest_run_id="j-1",
                latest_status="passed",
                latest_started_at=datetime.now(UTC),
                latest_app_count=3,
            ),
            RepoSummary(
                repo="org/r2",
                latest_run_id="j-2",
                latest_status="failed",
                latest_started_at=datetime.now(UTC),
                latest_app_count=2,
            ),
        ]
    )
    _, client = _make_app(qa_repo)

    resp = client.get("/v2/repos", headers={"Authorization": f"Bearer {_VALID_KEY}"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["repo"] == "org/r1"
    assert body[0]["latest_status"] == "passed"
    assert body[0]["latest_app_count"] == 3


def test_get_repos_requires_auth():
    qa_repo = AsyncMock()
    qa_repo.list_repos_with_runs = AsyncMock(return_value=[])
    _, client = _make_app(qa_repo)

    resp = client.get("/v2/repos")
    assert resp.status_code == 401


def test_get_repos_accepts_dashboard_session_cookie():
    """Cookie-based auth should also work (browser path)."""
    from athanor.api.auth import issue_dashboard_jwt

    qa_repo = AsyncMock()
    qa_repo.list_repos_with_runs = AsyncMock(return_value=[])
    _, client = _make_app(qa_repo)

    token, _ = issue_dashboard_jwt(api_key=_VALID_KEY, secret=_VALID_KEY)
    resp = client.get("/v2/repos", cookies={"dashboard_session": token})
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_repos_respects_limit_query_param():
    qa_repo = AsyncMock()
    qa_repo.list_repos_with_runs = AsyncMock(return_value=[])
    _, client = _make_app(qa_repo)

    resp = client.get("/v2/repos?limit=10", headers={"Authorization": f"Bearer {_VALID_KEY}"})
    assert resp.status_code == 200
    qa_repo.list_repos_with_runs.assert_called_once_with(limit=10)
