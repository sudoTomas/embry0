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


# ─── Phase 5E: cache_analytics_window ─────────────────────────────────────


async def _seed_job_with_repo(
    db: DatabasePool,
    job_id: str,
    repo_name: str,
    *,
    created_days_ago: int = 0,
) -> None:
    """Insert a jobs row pinned to ``repo_name`` with a specific created_at age.

    Uses NOW() - INTERVAL so the row falls inside or outside the analytics
    window deterministically without sleeping.
    """
    await db.execute(
        """
        INSERT INTO jobs (job_id, repo, task, created_at)
        VALUES ($1, $2, 'test', NOW() - ($3::int * INTERVAL '1 day'))
        ON CONFLICT (job_id) DO UPDATE
            SET repo = EXCLUDED.repo, created_at = EXCLUDED.created_at
        """,
        job_id,
        repo_name,
        int(created_days_ago),
    )


@pytest.mark.asyncio
async def test_cache_analytics_window_empty_repo(repo, db):
    """A repo with no qa_app_results rows returns the canonical zero payload."""
    result = await repo.cache_analytics_window(repo="org/empty", days=30)
    assert result == {
        "repo": "org/empty",
        "window_days": 30,
        "total_runs": 0,
        "total_subtasks": 0,
        "layers": [
            {"layer": "prebaked_image", "hits": 0, "misses": 0, "hit_ratio": 0.0},
            {"layer": "shared_volume", "hits": 0, "misses": 0, "hit_ratio": 0.0},
            {"layer": "turbo_remote", "hits": 0, "misses": 0, "hit_ratio": 0.0},
        ],
        "cold_cache_apps": [],
    }


@pytest.mark.asyncio
async def test_cache_analytics_window_all_hits(repo, db):
    """Every row hits all 3 layers — hit_ratio == 1.0 for each."""
    repo_name = "org/all-hits"
    await _seed_job_with_repo(db, "job-ah-1", repo_name)
    await _seed_job_with_repo(db, "job-ah-2", repo_name)

    cache_hits = CacheHits(
        prebaked_image=True,
        shared_volume=True,
        turbo_remote_hits=["apps/hub#build", "apps/hub#test"],
        turbo_remote_misses=[],
    )
    for job_id in ("job-ah-1", "job-ah-2"):
        await repo.upsert_with_boot_phase(
            job_id=job_id,
            app_name="hub",
            status="passed",
            duration_ms=100,
            cache_hits=cache_hits,
            raw_result={},
            boot_phase=None,
        )

    result = await repo.cache_analytics_window(repo=repo_name, days=30)
    assert result["repo"] == repo_name
    assert result["window_days"] == 30
    assert result["total_runs"] == 2
    assert result["total_subtasks"] == 2

    by_layer = {layer["layer"]: layer for layer in result["layers"]}
    assert by_layer["prebaked_image"] == {
        "layer": "prebaked_image",
        "hits": 2,
        "misses": 0,
        "hit_ratio": 1.0,
    }
    assert by_layer["shared_volume"] == {
        "layer": "shared_volume",
        "hits": 2,
        "misses": 0,
        "hit_ratio": 1.0,
    }
    assert by_layer["turbo_remote"] == {
        "layer": "turbo_remote",
        "hits": 4,
        "misses": 0,
        "hit_ratio": 1.0,
    }
    assert result["cold_cache_apps"] == []


@pytest.mark.asyncio
async def test_cache_analytics_window_partial_misses(repo, db):
    """Mixed hits/misses produce correctly weighted ratios."""
    repo_name = "org/mixed"
    await _seed_job_with_repo(db, "job-m-1", repo_name)
    await _seed_job_with_repo(db, "job-m-2", repo_name)

    # job-m-1, app=hub: prebaked hit, shared miss, turbo 3 hits / 1 miss
    await repo.upsert_with_boot_phase(
        job_id="job-m-1",
        app_name="hub",
        status="passed",
        duration_ms=100,
        cache_hits=CacheHits(
            prebaked_image=True,
            shared_volume=False,
            turbo_remote_hits=["a", "b", "c"],
            turbo_remote_misses=["d"],
        ),
        raw_result={},
        boot_phase=None,
    )
    # job-m-2, app=hub: prebaked miss, shared hit, turbo 1 hit / 1 miss
    await repo.upsert_with_boot_phase(
        job_id="job-m-2",
        app_name="hub",
        status="passed",
        duration_ms=100,
        cache_hits=CacheHits(
            prebaked_image=False,
            shared_volume=True,
            turbo_remote_hits=["a"],
            turbo_remote_misses=["b"],
        ),
        raw_result={},
        boot_phase=None,
    )

    result = await repo.cache_analytics_window(repo=repo_name, days=30)
    assert result["total_runs"] == 2
    assert result["total_subtasks"] == 2

    by_layer = {layer["layer"]: layer for layer in result["layers"]}
    assert by_layer["prebaked_image"]["hits"] == 1
    assert by_layer["prebaked_image"]["misses"] == 1
    assert by_layer["prebaked_image"]["hit_ratio"] == 0.5

    assert by_layer["shared_volume"]["hits"] == 1
    assert by_layer["shared_volume"]["misses"] == 1
    assert by_layer["shared_volume"]["hit_ratio"] == 0.5

    assert by_layer["turbo_remote"]["hits"] == 4
    assert by_layer["turbo_remote"]["misses"] == 2
    assert abs(by_layer["turbo_remote"]["hit_ratio"] - (4 / 6)) < 1e-9


@pytest.mark.asyncio
async def test_cache_analytics_window_filters_by_window(repo, db):
    """A row with created_at before the window boundary is NOT counted."""
    repo_name = "org/window"
    # Inside window
    await _seed_job_with_repo(db, "job-win-fresh", repo_name, created_days_ago=2)
    # Outside window (40 days old, default window is 30)
    await _seed_job_with_repo(db, "job-win-stale", repo_name, created_days_ago=40)

    fresh_hits = CacheHits(prebaked_image=True, shared_volume=True)
    stale_hits = CacheHits(prebaked_image=False, shared_volume=False)
    await repo.upsert_with_boot_phase(
        job_id="job-win-fresh",
        app_name="hub",
        status="passed",
        duration_ms=100,
        cache_hits=fresh_hits,
        raw_result={},
        boot_phase=None,
    )
    await repo.upsert_with_boot_phase(
        job_id="job-win-stale",
        app_name="hub",
        status="passed",
        duration_ms=100,
        cache_hits=stale_hits,
        raw_result={},
        boot_phase=None,
    )

    result = await repo.cache_analytics_window(repo=repo_name, days=30)
    # Stale row excluded — total_runs/subtasks reflect only the fresh row.
    assert result["total_runs"] == 1
    assert result["total_subtasks"] == 1
    by_layer = {layer["layer"]: layer for layer in result["layers"]}
    assert by_layer["prebaked_image"]["hits"] == 1
    assert by_layer["prebaked_image"]["misses"] == 0
    assert by_layer["shared_volume"]["hits"] == 1
    assert by_layer["shared_volume"]["misses"] == 0


@pytest.mark.asyncio
async def test_cache_analytics_window_cold_cache_apps_threshold(repo, db):
    """Cold-cache offenders require both ratio < 0.25 AND subtasks >= 3.

    Seeds:
      - 'cold-app': 5 sub-tasks, all misses (ratio = 0.0) → IS in offenders.
      - 'one-bad-run': 1 sub-task, all misses (ratio = 0.0) → NOT in offenders.
    """
    repo_name = "org/cold"
    # cold-app over 5 jobs, all misses
    for i in range(5):
        job_id = f"job-cold-{i}"
        await _seed_job_with_repo(db, job_id, repo_name)
        await repo.upsert_with_boot_phase(
            job_id=job_id,
            app_name="cold-app",
            status="passed",
            duration_ms=100,
            cache_hits=CacheHits(
                prebaked_image=False,
                shared_volume=False,
                turbo_remote_hits=[],
                turbo_remote_misses=["t1"],
            ),
            raw_result={},
            boot_phase=None,
        )
    # one-bad-run with a single bad sub-task
    await _seed_job_with_repo(db, "job-obr-1", repo_name)
    await repo.upsert_with_boot_phase(
        job_id="job-obr-1",
        app_name="one-bad-run",
        status="passed",
        duration_ms=100,
        cache_hits=CacheHits(
            prebaked_image=False,
            shared_volume=False,
            turbo_remote_hits=[],
            turbo_remote_misses=["t1"],
        ),
        raw_result={},
        boot_phase=None,
    )

    result = await repo.cache_analytics_window(repo=repo_name, days=30)
    assert "cold-app" in result["cold_cache_apps"]
    assert "one-bad-run" not in result["cold_cache_apps"]


@pytest.mark.asyncio
async def test_cache_analytics_window_filters_other_repos(repo, db):
    """Rows from a different repo never leak into the analytics for ``repo``."""
    await _seed_job_with_repo(db, "job-mine-1", "org/mine")
    await _seed_job_with_repo(db, "job-other-1", "org/other")

    await repo.upsert_with_boot_phase(
        job_id="job-mine-1",
        app_name="hub",
        status="passed",
        duration_ms=100,
        cache_hits=CacheHits(prebaked_image=True),
        raw_result={},
        boot_phase=None,
    )
    await repo.upsert_with_boot_phase(
        job_id="job-other-1",
        app_name="hub",
        status="passed",
        duration_ms=100,
        cache_hits=CacheHits(prebaked_image=False),
        raw_result={},
        boot_phase=None,
    )

    result = await repo.cache_analytics_window(repo="org/mine", days=30)
    assert result["total_runs"] == 1
    by_layer = {layer["layer"]: layer for layer in result["layers"]}
    assert by_layer["prebaked_image"]["hits"] == 1
    assert by_layer["prebaked_image"]["misses"] == 0


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
