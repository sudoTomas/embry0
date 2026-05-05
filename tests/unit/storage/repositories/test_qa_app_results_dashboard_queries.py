"""Phase-4 dashboard queries on QAAppResultsRepository.

Tests run against an in-memory Postgres via the existing pg_pool fixture.
Marked requires_postgres so they auto-skip without a DB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from athanor.storage.repositories.qa_app_results import (
    QAAppResultsRepository,
)
from athanor.workflows.qa.subtask_result_schema import (
    CacheHits,
    SubTaskResult,
    SubTaskStatus,
)

pytestmark = pytest.mark.requires_postgres


async def _seed_job(pg_pool, *, job_id: str, repo: str, started_at: datetime) -> None:
    async with pg_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO jobs (job_id, repo, status, created_at, updated_at, started_at)
            VALUES ($1, $2, 'completed', $3, $3, $3)
            ON CONFLICT (job_id) DO NOTHING
            """,
            job_id,
            repo,
            started_at,
        )


@pytest.mark.asyncio
async def test_list_repos_with_runs_returns_distinct_repos_latest_first(pg_pool):
    repo = QAAppResultsRepository(db=pg_pool)
    now = datetime.now(UTC)

    await _seed_job(pg_pool, job_id="j-old-r1", repo="org/r1", started_at=now - timedelta(hours=2))
    await _seed_job(pg_pool, job_id="j-new-r1", repo="org/r1", started_at=now - timedelta(minutes=5))
    await _seed_job(pg_pool, job_id="j-r2", repo="org/r2", started_at=now - timedelta(hours=1))

    for jid in ("j-old-r1", "j-new-r1", "j-r2"):
        await repo.upsert(
            jid,
            SubTaskResult(
                app_name="hub",
                status=SubTaskStatus.PASSED,
                duration_ms=1000,
                cache_hits=CacheHits(),
            ),
        )

    rows = await repo.list_repos_with_runs(limit=50)
    repos = [r.repo for r in rows]
    assert "org/r1" in repos
    assert "org/r2" in repos
    # Latest job id wins for org/r1
    by_repo = {r.repo: r for r in rows}
    assert by_repo["org/r1"].latest_run_id == "j-new-r1"


@pytest.mark.asyncio
async def test_list_runs_for_repo_returns_runs_newest_first(pg_pool):
    repo = QAAppResultsRepository(db=pg_pool)
    now = datetime.now(UTC)

    await _seed_job(pg_pool, job_id="r3-j1", repo="org/r3", started_at=now - timedelta(hours=2))
    await _seed_job(pg_pool, job_id="r3-j2", repo="org/r3", started_at=now - timedelta(hours=1))

    await repo.upsert("r3-j1", SubTaskResult("hub", SubTaskStatus.PASSED, 1000, CacheHits()))
    await repo.upsert("r3-j1", SubTaskResult("companion", SubTaskStatus.QA_FAILURE, 2000, CacheHits()))
    await repo.upsert("r3-j2", SubTaskResult("hub", SubTaskStatus.PASSED, 1500, CacheHits()))

    runs = await repo.list_runs_for_repo("org/r3", limit=10, offset=0)
    assert len(runs) == 2
    assert runs[0].job_id == "r3-j2"  # newest first
    assert runs[1].job_id == "r3-j1"
    # j1 has 2 apps, one failed → overall_status=failed
    j1 = runs[1]
    assert j1.app_count == 2
    assert j1.overall_status == "failed"
    j2 = runs[0]
    assert j2.app_count == 1
    assert j2.overall_status == "passed"


@pytest.mark.asyncio
async def test_list_history_for_app_returns_last_n(pg_pool):
    repo = QAAppResultsRepository(db=pg_pool)
    now = datetime.now(UTC)

    for i in range(3):
        jid = f"hist-{i}"
        await _seed_job(pg_pool, job_id=jid, repo="org/r4", started_at=now - timedelta(hours=i))
        await repo.upsert(
            jid,
            SubTaskResult(
                app_name="hub",
                status=SubTaskStatus.PASSED if i != 1 else SubTaskStatus.QA_FAILURE,
                duration_ms=1000 + i * 100,
                cache_hits=CacheHits(),
            ),
        )

    history = await repo.list_history_for_app("org/r4", "hub", limit=10)
    assert len(history) == 3
    # Newest first
    assert history[0].job_id == "hist-0"
    assert history[1].job_id == "hist-1"
    assert history[1].status == "qa_failure"
    assert history[2].job_id == "hist-2"


@pytest.mark.asyncio
async def test_list_runs_for_repo_pagination(pg_pool):
    repo = QAAppResultsRepository(db=pg_pool)
    now = datetime.now(UTC)
    for i in range(5):
        jid = f"page-{i}"
        await _seed_job(pg_pool, job_id=jid, repo="org/r5", started_at=now - timedelta(hours=i))
        await repo.upsert(jid, SubTaskResult("hub", SubTaskStatus.PASSED, 1000, CacheHits()))

    page1 = await repo.list_runs_for_repo("org/r5", limit=2, offset=0)
    page2 = await repo.list_runs_for_repo("org/r5", limit=2, offset=2)
    assert [r.job_id for r in page1] == ["page-0", "page-1"]
    assert [r.job_id for r in page2] == ["page-2", "page-3"]


# ─── Three-way rollup tests (bug_004) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_three_way_rollup_infra_failure_yields_infra_error(pg_pool):
    """2 passed apps + 1 infra_failure → latest_status / overall_status == 'infra_error'."""
    repo = QAAppResultsRepository(db=pg_pool)
    now = datetime.now(UTC)
    await _seed_job(pg_pool, job_id="rollup-infra", repo="org/rollup1", started_at=now)

    await repo.upsert("rollup-infra", SubTaskResult("app-a", SubTaskStatus.PASSED, 1000, CacheHits()))
    await repo.upsert("rollup-infra", SubTaskResult("app-b", SubTaskStatus.PASSED, 1200, CacheHits()))
    await repo.upsert(
        "rollup-infra",
        SubTaskResult("app-c", SubTaskStatus.INFRA_FAILURE, 300, CacheHits()),
    )

    runs = await repo.list_runs_for_repo("org/rollup1", limit=10, offset=0)
    assert len(runs) == 1
    assert runs[0].overall_status == "infra_error"

    repos = await repo.list_repos_with_runs(limit=10)
    by_repo = {r.repo: r for r in repos}
    assert by_repo["org/rollup1"].latest_status == "infra_error"


@pytest.mark.asyncio
async def test_three_way_rollup_qa_failure_yields_failed(pg_pool):
    """2 passed apps + 1 qa_failure → overall_status == 'failed'."""
    repo = QAAppResultsRepository(db=pg_pool)
    now = datetime.now(UTC)
    await _seed_job(pg_pool, job_id="rollup-qa", repo="org/rollup2", started_at=now)

    await repo.upsert("rollup-qa", SubTaskResult("app-a", SubTaskStatus.PASSED, 1000, CacheHits()))
    await repo.upsert("rollup-qa", SubTaskResult("app-b", SubTaskStatus.PASSED, 1200, CacheHits()))
    await repo.upsert(
        "rollup-qa",
        SubTaskResult("app-c", SubTaskStatus.QA_FAILURE, 2000, CacheHits()),
    )

    runs = await repo.list_runs_for_repo("org/rollup2", limit=10, offset=0)
    assert len(runs) == 1
    assert runs[0].overall_status == "failed"

    repos = await repo.list_repos_with_runs(limit=10)
    by_repo = {r.repo: r for r in repos}
    assert by_repo["org/rollup2"].latest_status == "failed"


@pytest.mark.asyncio
async def test_three_way_rollup_all_passed_yields_passed(pg_pool):
    """2 passed apps → overall_status == 'passed'."""
    repo = QAAppResultsRepository(db=pg_pool)
    now = datetime.now(UTC)
    await _seed_job(pg_pool, job_id="rollup-pass", repo="org/rollup3", started_at=now)

    await repo.upsert("rollup-pass", SubTaskResult("app-a", SubTaskStatus.PASSED, 1000, CacheHits()))
    await repo.upsert("rollup-pass", SubTaskResult("app-b", SubTaskStatus.PASSED, 1200, CacheHits()))

    runs = await repo.list_runs_for_repo("org/rollup3", limit=10, offset=0)
    assert len(runs) == 1
    assert runs[0].overall_status == "passed"

    repos = await repo.list_repos_with_runs(limit=10)
    by_repo = {r.repo: r for r in repos}
    assert by_repo["org/rollup3"].latest_status == "passed"
