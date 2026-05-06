"""Route tests for the /api/v1/qa/... dashboard surface.

Consolidates tests for all 6 routes:
  GET /api/v1/qa/repos
  GET /api/v1/qa/repos/{repo}/runs
  GET /api/v1/qa/repos/{repo}/apps/{app}/history
  GET /api/v1/qa/runs/{run_id}
  GET /api/v1/qa/runs/{run_id}/apps/{app}
  GET /api/v1/qa/runs/{run_id}/affected_set
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from athanor.storage.repositories.qa_app_results import (
    AppHistoryRow,
    QAAppResultRow,
    RepoSummary,
    RunSummary,
)
from athanor.storage.repositories.qa_run_metadata import QARunMetadata
from athanor.workflows.qa.subtask_result_schema import CacheHits, SubTaskStatus

_KEY = "test-api-key-32-characters-minimum-x"
_AUTH = {"Authorization": f"Bearer {_KEY}"}


def _make_app(qa_repo=None, jobs_repo=None, run_md_repo=None):
    from athanor.api.app import create_app

    app = create_app()

    class _Cfg:
        pass

    cfg = _Cfg()
    cfg.api_key = _KEY
    cfg.auth_dev_mode = False
    cfg.webhook_dev_mode = False
    app.state.config = cfg
    if qa_repo is not None:
        app.state.qa_app_results_repo = qa_repo
    if jobs_repo is not None:
        app.state.jobs_repo = jobs_repo
    if run_md_repo is not None:
        app.state.qa_run_metadata_repo = run_md_repo
    return app, TestClient(app)


# ─── GET /api/v1/qa/repos ────────────────────────────────────────────────────


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
    _, client = _make_app(qa_repo=qa_repo)

    resp = client.get("/api/v1/qa/repos", headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["repo"] == "org/r1"
    assert body[0]["latest_status"] == "passed"
    assert body[0]["latest_app_count"] == 3


def test_get_repos_requires_auth():
    qa_repo = AsyncMock()
    qa_repo.list_repos_with_runs = AsyncMock(return_value=[])
    _, client = _make_app(qa_repo=qa_repo)

    resp = client.get("/api/v1/qa/repos")
    assert resp.status_code == 401


def test_get_repos_respects_limit_query_param():
    qa_repo = AsyncMock()
    qa_repo.list_repos_with_runs = AsyncMock(return_value=[])
    _, client = _make_app(qa_repo=qa_repo)

    resp = client.get("/api/v1/qa/repos?limit=10", headers=_AUTH)
    assert resp.status_code == 200
    qa_repo.list_repos_with_runs.assert_called_once_with(limit=10)


# ─── GET /api/v1/qa/repos/{repo}/runs ────────────────────────────────────────


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
    _, client = _make_app(qa_repo=qa_repo)
    resp = client.get(
        "/api/v1/qa/repos/org%2Fr1/runs?limit=3&offset=0",
        headers=_AUTH,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert body[0]["job_id"] == "j-0"
    qa_repo.list_runs_for_repo.assert_called_once_with("org/r1", limit=3, offset=0)


def test_list_runs_for_repo_default_pagination():
    qa_repo = AsyncMock()
    qa_repo.list_runs_for_repo = AsyncMock(return_value=[])
    _, client = _make_app(qa_repo=qa_repo)
    resp = client.get(
        "/api/v1/qa/repos/org%2Fr2/runs",
        headers=_AUTH,
    )
    assert resp.status_code == 200
    qa_repo.list_runs_for_repo.assert_called_once_with("org/r2", limit=50, offset=0)


def test_list_runs_for_repo_requires_auth():
    qa_repo = AsyncMock()
    _, client = _make_app(qa_repo=qa_repo)
    resp = client.get("/api/v1/qa/repos/org%2Fr1/runs")
    assert resp.status_code == 401


# ─── GET /api/v1/qa/repos/{repo}/apps/{app}/history ──────────────────────────


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
    _, client = _make_app(qa_repo=qa_repo)
    resp = client.get(
        "/api/v1/qa/repos/org%2Fr1/apps/hub/history?limit=10",
        headers=_AUTH,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert body[1]["status"] == "qa_failure"
    qa_repo.list_history_for_app.assert_called_once_with("org/r1", "hub", limit=10)


def test_get_app_history_default_limit():
    qa_repo = AsyncMock()
    qa_repo.list_history_for_app = AsyncMock(return_value=[])
    _, client = _make_app(qa_repo=qa_repo)
    resp = client.get(
        "/api/v1/qa/repos/org%2Fr1/apps/hub/history",
        headers=_AUTH,
    )
    assert resp.status_code == 200
    qa_repo.list_history_for_app.assert_called_once_with("org/r1", "hub", limit=20)


def test_get_app_history_requires_auth():
    qa_repo = AsyncMock()
    _, client = _make_app(qa_repo=qa_repo)
    resp = client.get("/api/v1/qa/repos/org%2Fr1/apps/hub/history")
    assert resp.status_code == 401


# ─── GET /api/v1/qa/runs/{run_id} ────────────────────────────────────────────


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
    _, client = _make_app(qa_repo=qa_repo, jobs_repo=jobs_repo)

    resp = client.get("/api/v1/qa/runs/job-1", headers=_AUTH)
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
    _, client = _make_app(qa_repo=qa_repo, jobs_repo=jobs_repo)
    resp = client.get("/api/v1/qa/runs/no-such-job", headers=_AUTH)
    assert resp.status_code == 404


def test_get_run_detail_requires_auth():
    qa_repo = AsyncMock()
    jobs_repo = AsyncMock()
    _, client = _make_app(qa_repo=qa_repo, jobs_repo=jobs_repo)
    resp = client.get("/api/v1/qa/runs/job-1")
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
    _, client = _make_app(qa_repo=qa_repo, jobs_repo=jobs_repo)
    resp = client.get("/api/v1/qa/runs/j", headers=_AUTH)
    assert resp.json()["overall_status"] == "passed"


def test_get_run_detail_overall_status_infra_error_when_any_infra_failure():
    """INFRA_FAILURE on any app short-circuits to 'infra_error' (bug_004)."""
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
            QAAppResultRow(
                job_id="j",
                app_name="infra-broken",
                status=SubTaskStatus.INFRA_FAILURE,
                duration_ms=500,
                cache_hits=CacheHits(),
                trace_url=None,
                failure_summary="docker pull failed",
                raw_result={},
            ),
        ]
    )
    jobs_repo = AsyncMock()
    jobs_repo.get = AsyncMock(
        return_value={"job_id": "j", "repo": "org/r1", "started_at": datetime.now(UTC)}
    )
    _, client = _make_app(qa_repo=qa_repo, jobs_repo=jobs_repo)
    resp = client.get("/api/v1/qa/runs/j", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["overall_status"] == "infra_error"


def test_get_run_detail_overall_status_skipped_is_pass():
    """SKIPPED rows do not cause a 'failed' rollup (bug_004 — SKIPPED is a pass)."""
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
            QAAppResultRow(
                job_id="j",
                app_name="skipped-app",
                status=SubTaskStatus.SKIPPED,
                duration_ms=0,
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
    _, client = _make_app(qa_repo=qa_repo, jobs_repo=jobs_repo)
    resp = client.get("/api/v1/qa/runs/j", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["overall_status"] == "passed"


# ─── GET /api/v1/qa/runs/{run_id}/apps/{app} ─────────────────────────────────


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
    _, client = _make_app(qa_repo=qa_repo)
    resp = client.get("/api/v1/qa/runs/j-1/apps/companion", headers=_AUTH)
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
    _, client = _make_app(qa_repo=qa_repo)
    resp = client.get("/api/v1/qa/runs/j-1/apps/notfound", headers=_AUTH)
    assert resp.status_code == 404


# ─── GET /api/v1/qa/runs/{run_id}/affected_set (Phase 5D) ────────────────────


def test_get_affected_set_returns_full_metadata():
    md_repo = AsyncMock()
    md_repo.get = AsyncMock(
        return_value=QARunMetadata(
            job_id="run-md-1",
            apps_to_qa=["hub"],
            apps_skipped=["companion", "lane"],
            force_all_apps=False,
            changed_files=["apps/hub/app/page.tsx"],
            base_branch="main",
            dep_graph=[
                {"source": "@x/hub", "target": "@x/types"},
            ],
        )
    )
    _, client = _make_app(run_md_repo=md_repo)

    resp = client.get("/api/v1/qa/runs/run-md-1/affected_set", headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == "run-md-1"
    assert body["apps_to_qa"] == ["hub"]
    assert body["apps_skipped"] == ["companion", "lane"]
    assert body["force_all_apps"] is False
    assert body["changed_files"] == ["apps/hub/app/page.tsx"]
    assert body["base_branch"] == "main"
    assert body["dep_graph"] == [{"source": "@x/hub", "target": "@x/types"}]


def test_get_affected_set_404_when_missing():
    md_repo = AsyncMock()
    md_repo.get = AsyncMock(return_value=None)
    _, client = _make_app(run_md_repo=md_repo)

    resp = client.get("/api/v1/qa/runs/no-such/affected_set", headers=_AUTH)
    assert resp.status_code == 404


def test_get_affected_set_requires_auth():
    md_repo = AsyncMock()
    md_repo.get = AsyncMock(return_value=None)
    _, client = _make_app(run_md_repo=md_repo)

    resp = client.get("/api/v1/qa/runs/run-md-1/affected_set")
    assert resp.status_code == 401


# ─── GET /api/v1/qa/repos/{repo}/cache/analytics (Phase 5E) ─────────────────


def _empty_layers():
    return [
        {"layer": "prebaked_image", "hits": 0, "misses": 0, "hit_ratio": 0.0},
        {"layer": "shared_volume", "hits": 0, "misses": 0, "hit_ratio": 0.0},
        {"layer": "turbo_remote", "hits": 0, "misses": 0, "hit_ratio": 0.0},
    ]


def test_cache_analytics_route_default_window():
    qa_repo = AsyncMock()
    qa_repo.cache_analytics_window = AsyncMock(
        return_value={
            "repo": "org/r1",
            "window_days": 30,
            "total_runs": 12,
            "total_subtasks": 36,
            "layers": [
                {"layer": "prebaked_image", "hits": 30, "misses": 6, "hit_ratio": 0.8333},
                {"layer": "shared_volume", "hits": 20, "misses": 16, "hit_ratio": 0.5556},
                {"layer": "turbo_remote", "hits": 80, "misses": 40, "hit_ratio": 0.6667},
            ],
            "cold_cache_apps": ["legacy-app"],
        }
    )
    _, client = _make_app(qa_repo=qa_repo)

    resp = client.get("/api/v1/qa/repos/org%2Fr1/cache/analytics", headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["repo"] == "org/r1"
    assert body["window_days"] == 30
    assert body["total_runs"] == 12
    assert body["total_subtasks"] == 36
    assert len(body["layers"]) == 3
    by_layer = {layer["layer"]: layer for layer in body["layers"]}
    assert by_layer["prebaked_image"]["hits"] == 30
    assert by_layer["shared_volume"]["hits"] == 20
    assert by_layer["turbo_remote"]["hits"] == 80
    assert body["cold_cache_apps"] == ["legacy-app"]
    qa_repo.cache_analytics_window.assert_called_once_with(repo="org/r1", days=30)


def test_cache_analytics_route_custom_window():
    qa_repo = AsyncMock()
    qa_repo.cache_analytics_window = AsyncMock(
        return_value={
            "repo": "org/r1",
            "window_days": 7,
            "total_runs": 0,
            "total_subtasks": 0,
            "layers": _empty_layers(),
            "cold_cache_apps": [],
        }
    )
    _, client = _make_app(qa_repo=qa_repo)

    resp = client.get(
        "/api/v1/qa/repos/org%2Fr1/cache/analytics?window_days=7",
        headers=_AUTH,
    )
    assert resp.status_code == 200
    qa_repo.cache_analytics_window.assert_called_once_with(repo="org/r1", days=7)


def test_cache_analytics_route_window_validation():
    qa_repo = AsyncMock()
    qa_repo.cache_analytics_window = AsyncMock()
    _, client = _make_app(qa_repo=qa_repo)

    resp = client.get(
        "/api/v1/qa/repos/org%2Fr1/cache/analytics?window_days=400",
        headers=_AUTH,
    )
    assert resp.status_code == 422
    qa_repo.cache_analytics_window.assert_not_called()

    # Also reject 0 / negatives.
    resp = client.get(
        "/api/v1/qa/repos/org%2Fr1/cache/analytics?window_days=0",
        headers=_AUTH,
    )
    assert resp.status_code == 422


def test_cache_analytics_route_auth_required():
    qa_repo = AsyncMock()
    qa_repo.cache_analytics_window = AsyncMock()
    _, client = _make_app(qa_repo=qa_repo)

    resp = client.get("/api/v1/qa/repos/org%2Fr1/cache/analytics")
    assert resp.status_code == 401
    qa_repo.cache_analytics_window.assert_not_called()


def test_get_affected_set_with_empty_dep_graph_and_force_all():
    """Phase-5D normal case: dep_graph empty (provider does not yet expose
    edges) and force_all_apps=True (qa_required=always)."""
    md_repo = AsyncMock()
    md_repo.get = AsyncMock(
        return_value=QARunMetadata(
            job_id="run-md-force",
            apps_to_qa=["hub", "companion"],
            apps_skipped=[],
            force_all_apps=True,
            changed_files=[],
            base_branch="",
            dep_graph=[],
        )
    )
    _, client = _make_app(run_md_repo=md_repo)

    resp = client.get("/api/v1/qa/runs/run-md-force/affected_set", headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["force_all_apps"] is True
    assert body["apps_skipped"] == []
    assert body["dep_graph"] == []
    assert body["changed_files"] == []
    assert body["base_branch"] == ""
