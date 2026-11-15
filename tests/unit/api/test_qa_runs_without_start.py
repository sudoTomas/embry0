"""A QA run's reads with a job that never recorded a start (#481).

A job with QA app results and no ``started_at`` is no run: the repositories
list (``list_repos_with_runs``) already left it out. The runs list and an
app's history answered 500 instead (their models need a start), and so did the
run's detail. All three now leave it out, beside a run that started.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

REPO = "acme/widgets"


async def _job(db, job_id: str, *, started: bool, status: str = "passed") -> None:
    await db.execute(
        "INSERT INTO jobs (job_id, task, repo, status, started_at) VALUES ($1, 't', $2, 'completed', "
        + ("now()" if started else "NULL")
        + ")",
        job_id,
        REPO,
    )
    await db.execute(
        "INSERT INTO qa_app_results (job_id, app_name, status) VALUES ($1, 'web', $2)",
        job_id,
        status,
    )


@pytest.mark.parametrize("status", ["passed", "qa_failure"])
async def test_a_job_with_results_and_no_start_is_no_run(api_client: AsyncClient, status: str) -> None:
    db = api_client.app.state.db  # type: ignore[attr-defined]
    await _job(db, "job-started", started=True, status=status)
    await _job(db, "job-unstarted", started=False, status=status)

    runs = await api_client.get(f"/api/v1/qa/repos/{REPO}/runs")
    assert runs.status_code == 200, runs.text[:300]
    assert [r["job_id"] for r in runs.json()] == ["job-started"], runs.json()

    history = await api_client.get(f"/api/v1/qa/repos/{REPO}/apps/web/history")
    assert history.status_code == 200, history.text[:300]
    assert [h["job_id"] for h in history.json()] == ["job-started"], history.json()

    started = await api_client.get("/api/v1/qa/runs/job-started")
    assert started.status_code == 200, started.text[:300]
    unstarted = await api_client.get("/api/v1/qa/runs/job-unstarted")
    assert unstarted.status_code == 404, f"{unstarted.status_code}: {unstarted.text[:300]}"
    assert "never started" in unstarted.json()["detail"], unstarted.json()

    repos = await api_client.get("/api/v1/qa/repos")
    assert [r["latest_run_id"] for r in repos.json()] == ["job-started"], "the repositories list, as before"
