from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config


@pytest.fixture
def app():
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config)
    mock_db = MagicMock()
    mock_db.fetchval = AsyncMock(return_value=0)
    mock_db.fetchrow = AsyncMock(
        return_value={
            "total_jobs": 0,
            "completed": 0,
            "failed": 0,
            "partial": 0,
            "running": 0,
            "queued": 0,
            "awaiting_input": 0,
            "paused": 0,
            "total_cost": 0.0,
            "daily_cost": 0.0,
            "monthly_cost": 0.0,
        }
    )
    mock_db.fetch = AsyncMock(return_value=[])
    app.state.db = mock_db
    mock_traces = MagicMock()
    mock_traces.list_all = AsyncMock(return_value=([], 0))
    app.state.traces_repo = mock_traces
    mock_jobs = MagicMock()
    mock_jobs.queue_summary = AsyncMock(return_value={"pending": 0, "running": 0, "awaiting_input": 0, "paused": 0})
    app.state.jobs_repo = mock_jobs
    return app


@pytest.mark.asyncio
async def test_get_stats(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_jobs" in data
    assert "total_issues" in data
    assert "success_rate" in data
    assert "daily_cost_usd" in data
    assert "recent_issues" in data


@pytest.mark.asyncio
async def test_list_traces(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/traces")
    assert resp.status_code == 200
    assert "traces" in resp.json()


@pytest.mark.asyncio
async def test_get_queue(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/queue")
    assert resp.status_code == 200
    data = resp.json()
    assert "depth" in data
    assert "pending" in data
    assert "running" in data
    assert "awaiting_input" in data
    assert isinstance(data["paused"], int)


@pytest.mark.asyncio
async def test_stats_groups_failed_jobs_into_failure_categories(app):
    """#281: failure_categories was a hardcoded {}."""

    async def fetch(sql, *args):
        if "GROUP BY error_code" in sql:
            return [
                {"error_code": "ERR_AGENT_MAX_TURNS", "n": 19},
                {"error_code": "ERR_AGENT_PROCESS_FAILED", "n": 5},
                {"error_code": "ERR_QA_INFRA", "n": 10},
                {"error_code": "ERR_BUDGET_OVERRUN", "n": 2},
                {"error_code": "ERR_UNKNOWN", "n": 3},
                {"error_code": None, "n": 1},
                {"error_code": "ERR_WRITTEN_BY_A_NEWER_DEPLOY", "n": 4},
            ]
        return []

    app.state.db.fetch = AsyncMock(side_effect=fetch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/stats")
    assert resp.status_code == 200
    assert resp.json()["failure_categories"] == {
        "infra": 10,
        "agent": 24,
        "verification": 0,
        "budget": 2,
        "config": 0,
        "cancelled": 0,
        "unknown": 8,
    }


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_stats_report_job_outcome_and_delivery_apart(db_with_migrations):
    """#280: a failed job whose PR merged is delivered work, not a failure —
    and a completed job does not stop counting as a success when its PR merges."""
    db = db_with_migrations
    # (status, delivery_state, pr_url) — the two `failed`/`merged` rows share
    # ONE pull request, which is how a retry and its follow-up look.
    rows = [
        ("completed", "none", None),  # research job, no PR
        ("completed", "pr_open", "pr/1"),
        ("pr_merged", "merged", "pr/2"),
        ("pr_closed", "closed", "pr/3"),
        ("failed", "merged", "pr/4"),  # the 2026-09-20 case
        ("cancelled", "merged", "pr/4"),  # same PR as the row above
        ("failed", "closed", "pr/5"),
        ("failed", "none", None),
        ("running", "pr_open", "pr/1"),  # same PR as the open one above
        ("partial", "none", None),
        ("skipped", "none", None),  # #474: did no work — out of the rates
    ]
    for n, (status, delivery, pr_url) in enumerate(rows):
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status, delivery_state, pr_url) VALUES ($1, 'o/r', 't', $2, $3, $4)",
            f"job-{n}",
            status,
            delivery,
            pr_url,
        )
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    app.state.db = db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        data = (await client.get("/api/v1/stats")).json()

    # existing keys keep their meaning
    assert (data["total_jobs"], data["completed"], data["failed"], data["partial"]) == (11, 2, 3, 1)
    assert (data["job_succeeded"], data["cancelled"], data["skipped"]) == (4, 1, 1)
    assert data["job_success_rate"] == pytest.approx(0.4)
    assert data["success_rate"] == pytest.approx(0.6)  # 4 succeeded + 2 delivered despite their status
    # Jobs and PRs are counted apart: 3 job rows are `merged` but they are
    # only 2 distinct pull requests, and the merge rate is a PR rate.
    assert data["delivery"] == {
        "none": 4,
        "pr_open": 2,
        "merged": 3,
        "closed": 2,
        "merged_despite_job_status": 2,
        "prs_merged": 2,
        "prs_closed": 2,
        "prs_open": 1,
        "merge_rate": pytest.approx(0.5),
    }


@pytest.mark.asyncio
async def test_stats_survive_a_row_without_the_new_columns(app):
    """The mocked row above predates #280: missing aggregates read as zero."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        data = (await client.get("/api/v1/stats")).json()
    assert data["delivery"] == {
        "none": 0,
        "pr_open": 0,
        "merged": 0,
        "closed": 0,
        "merged_despite_job_status": 0,
        "prs_merged": 0,
        "prs_closed": 0,
        "prs_open": 0,
        "merge_rate": 0.0,
    }
    assert (data["success_rate"], data["job_success_rate"]) == (0.0, 0.0)


# --- #334: stale pre-built QA images are visible in /stats ------------------


@pytest.mark.asyncio
async def test_stats_reports_prebuilt_image_freshness(app):
    from datetime import UTC, datetime

    from embry0.storage.repositories.qa_image_tags import QAImageTagRow

    row = QAImageTagRow(
        repo="org/stale",
        image_tag="s:1",
        lockfile_sha="x",
        built_at=datetime(2026, 9, 1, tzinfo=UTC),
        built_by="cli",
        is_active=True,
        metadata={},
        base_image="registry:5000/embry0-sandbox-qa:latest",
        base_image_digest="sha256:" + "1" * 64,
    )
    app.state.qa_image_tags_repo = MagicMock(list_active=AsyncMock(return_value=[row]))
    app.state.qa_base_digest_resolver = MagicMock(current=AsyncMock(return_value="sha256:" + "2" * 64))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        data = (await client.get("/api/v1/stats")).json()

    assert data["qa_prebuilt_images"] == {
        "active": 1,
        "fresh": 0,
        "stale": [{"repo": "org/stale", "image_tag": "s:1"}],
        "unknown": [],
    }


@pytest.mark.asyncio
async def test_stats_survives_a_failing_prebuilt_image_summary(app):
    app.state.qa_image_tags_repo = MagicMock(list_active=AsyncMock(side_effect=RuntimeError("db down")))
    app.state.qa_base_digest_resolver = MagicMock()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/stats")

    assert resp.status_code == 200
    assert resp.json()["qa_prebuilt_images"] is None


@pytest.mark.asyncio
async def test_stats_without_a_docker_handle_reports_no_summary(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/stats")).json()["qa_prebuilt_images"] is None


@pytest.mark.asyncio
async def test_stats_reports_the_credential_registry(app):
    app.state.credentials_repo = MagicMock(
        list_all=AsyncMock(return_value=[{"status": "ok"}, {"status": "quarantined"}])
    )
    app.state.credential_resolver = MagicMock(env_fallback_keys=MagicMock(return_value=["GITHUB_TOKEN"]))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        data = (await client.get("/api/v1/stats")).json()
    assert data["credentials"] == {"count": 2, "quarantined": 1, "disabled": 0, "env_fallback_keys": ["GITHUB_TOKEN"]}


@pytest.mark.asyncio
async def test_stats_without_a_credential_registry_reports_none(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        data = (await client.get("/api/v1/stats")).json()
    assert "credentials" in data
    assert data["credentials"] is None


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_stats_recent_issues_name_each_issue(db_with_migrations):
    """Overview's Recent issues read 'repo#0' with no title for every
    non-GitHub ticket and showed the latest job's status as the issue's.
    Each row now carries the issue's title, tracker key and own status,
    with the latest job's status apart."""
    db = db_with_migrations
    await db.execute(
        "INSERT INTO issues (id, repo, title, status, tracker, tracker_key, created_at)"
        " VALUES ('iss-gh', 'o/r', 'Fix the login redirect', 'closed', 'github', '42', NOW() - interval '2 hours')"
    )
    await db.execute(
        "INSERT INTO issues (id, repo, title, status, tracker, tracker_key, created_at)"
        " VALUES ('iss-lin', 'o/r', 'Tidy the setup docs', 'open', 'linear', 'ENG-7', NOW() - interval '1 hour')"
    )
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, issue_id, started_at)"
        " VALUES ('job-1', 'o/r', 't', 'pr_merged', 'iss-gh', NOW() - interval '90 minutes')"
    )
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    app.state.db = db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        recent = (await client.get("/api/v1/stats")).json()["recent_issues"]

    by_id = {r["trace_id"]: r for r in recent}
    assert by_id["iss-lin"]["title"] == "Tidy the setup docs"
    assert (by_id["iss-lin"]["tracker"], by_id["iss-lin"]["tracker_key"]) == ("linear", "ENG-7")
    assert by_id["iss-lin"]["issue_status"] == "open"
    assert by_id["iss-lin"]["job_status"] is None
    assert by_id["iss-gh"]["title"] == "Fix the login redirect"
    assert (by_id["iss-gh"]["tracker"], by_id["iss-gh"]["tracker_key"]) == ("github", "42")
    assert (by_id["iss-gh"]["issue_status"], by_id["iss-gh"]["job_status"]) == ("closed", "pr_merged")
    # The existing fields keep their meaning for older clients.
    assert by_id["iss-gh"]["issue_number"] == 42
    assert by_id["iss-gh"]["status"] == "pr_merged"
