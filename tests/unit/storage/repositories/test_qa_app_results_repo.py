"""Tests for QAAppResultsRepository — idempotent upsert + list_for_job."""

from __future__ import annotations

import pytest

from athanor.storage.database import DatabasePool
from athanor.storage.repositories.qa_app_results import (
    QAAppResultsRepository,
)
from athanor.workflows.qa.subtask_result_schema import (
    CacheHits,
    SubTaskResult,
    SubTaskStatus,
)

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def repo(db_with_migrations: DatabasePool) -> QAAppResultsRepository:
    return QAAppResultsRepository(db=db_with_migrations)


@pytest.fixture
async def db(db_with_migrations: DatabasePool) -> DatabasePool:
    return db_with_migrations


def _result(app: str, status: SubTaskStatus, duration_ms: int = 100, **kw) -> SubTaskResult:
    return SubTaskResult(
        app_name=app,
        status=status,
        duration_ms=duration_ms,
        cache_hits=CacheHits(),
        trace_url=kw.get("trace_url"),
        failure_summary=kw.get("failure_summary"),
        raw_result=kw.get("raw_result", {"x": 1}),
    )


async def _seed_job(db: DatabasePool, job_id: str) -> None:
    """Insert a minimal jobs row so qa_app_results FK is satisfied."""
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task) VALUES ($1, 'org/repo', 'test')"
        " ON CONFLICT (job_id) DO NOTHING",
        job_id,
    )


@pytest.mark.asyncio
async def test_upsert_inserts_then_replaces_on_retry(repo, db):
    job_id = "test-upsert-replace"
    await _seed_job(db, job_id)

    await repo.upsert(job_id, _result("hub", SubTaskStatus.QA_FAILURE, duration_ms=100))
    await repo.upsert(job_id, _result("hub", SubTaskStatus.PASSED, duration_ms=200))

    rows = await repo.list_for_job(job_id)
    assert len(rows) == 1
    assert rows[0].status == SubTaskStatus.PASSED
    assert rows[0].duration_ms == 200


@pytest.mark.asyncio
async def test_list_for_job_empty(repo, db):
    job_id = "test-empty"
    await _seed_job(db, job_id)
    assert await repo.list_for_job(job_id) == []


@pytest.mark.asyncio
async def test_upsert_two_apps_different_rows(repo, db):
    job_id = "test-two-apps"
    await _seed_job(db, job_id)

    await repo.upsert(job_id, _result("hub", SubTaskStatus.PASSED))
    await repo.upsert(
        job_id,
        _result("companion", SubTaskStatus.QA_FAILURE, failure_summary="bad"),
    )

    rows = await repo.list_for_job(job_id)
    by_app = {r.app_name: r for r in rows}
    assert by_app["hub"].status == SubTaskStatus.PASSED
    assert by_app["companion"].status == SubTaskStatus.QA_FAILURE
    assert by_app["companion"].failure_summary == "bad"


@pytest.mark.asyncio
async def test_upsert_preserves_cache_hits_roundtrip(repo, db):
    job_id = "test-cache-hits"
    await _seed_job(db, job_id)

    result = SubTaskResult(
        app_name="hub",
        status=SubTaskStatus.PASSED,
        duration_ms=50,
        cache_hits=CacheHits(
            prebaked_image=True,
            shared_volume=False,
            turbo_remote_hits=["apps/hub#build"],
            turbo_remote_misses=["apps/companion#build"],
        ),
        trace_url="https://x/trace.zip",
    )
    await repo.upsert(job_id, result)

    rows = await repo.list_for_job(job_id)
    assert len(rows) == 1
    assert rows[0].cache_hits.prebaked_image is True
    assert rows[0].cache_hits.shared_volume is False
    assert rows[0].cache_hits.turbo_remote_hits == ["apps/hub#build"]
    assert rows[0].cache_hits.turbo_remote_misses == ["apps/companion#build"]
    assert rows[0].trace_url == "https://x/trace.zip"
