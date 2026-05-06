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
async def test_upsert_persists_boot_phase_payload(repo, db):
    """Phase 5A: boot_phase JSONB round-trips through the upsert path."""
    job_id = "test-boot-phase-roundtrip"
    await _seed_job(db, job_id)

    boot_phase = {
        "outcome": "timeout",
        "attempts": 12,
        "duration_ms": 600_000,
        "failed_checks": [
            "http://localhost:3000/health: got 503 expected 200",
        ],
        "boot_stdout_tail": "ready - started server on http://localhost:3000\n",
    }
    await repo.upsert_with_boot_phase(
        job_id=job_id,
        app_name="hub",
        status="boot_failure",
        duration_ms=600_000,
        cache_hits=CacheHits(),
        trace_url=None,
        failure_summary="boot_command did not become ready within 600s",
        raw_result={},
        boot_phase=boot_phase,
    )

    rows = await repo.list_for_job(job_id)
    assert len(rows) == 1
    assert rows[0].boot_phase == boot_phase


@pytest.mark.asyncio
async def test_upsert_with_boot_phase_none_stores_null(repo, db):
    """boot_phase=None must write SQL NULL — not the JSON string 'null'."""
    job_id = "test-boot-phase-null"
    await _seed_job(db, job_id)

    await repo.upsert_with_boot_phase(
        job_id=job_id,
        app_name="hub",
        status="passed",
        duration_ms=1500,
        cache_hits=CacheHits(),
        trace_url=None,
        failure_summary=None,
        raw_result={},
        boot_phase=None,
    )

    # Check the raw column value is SQL NULL, not the string 'null'.
    row = await db.fetchrow(
        "SELECT boot_phase FROM qa_app_results WHERE job_id=$1 AND app_name=$2",
        job_id,
        "hub",
    )
    assert row is not None
    assert row["boot_phase"] is None

    rows = await repo.list_for_job(job_id)
    assert len(rows) == 1
    assert rows[0].boot_phase is None


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
