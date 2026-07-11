"""Tests for QARunMetadataRepository — Phase 5D affected-set persistence."""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.qa_run_metadata import (
    QARunMetadata,
    QARunMetadataRepository,
)

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def repo(db_with_migrations: DatabasePool) -> QARunMetadataRepository:
    return QARunMetadataRepository(db=db_with_migrations)


@pytest.fixture
async def db(db_with_migrations: DatabasePool) -> DatabasePool:
    return db_with_migrations


async def _seed_job(db: DatabasePool, job_id: str) -> None:
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task) VALUES ($1, 'org/repo', 'test') ON CONFLICT (job_id) DO NOTHING",
        job_id,
    )


@pytest.mark.asyncio
async def test_get_returns_none_when_missing(repo):
    assert await repo.get("does-not-exist") is None


@pytest.mark.asyncio
async def test_upsert_then_get_round_trips_all_fields(repo, db):
    job_id = "test-md-roundtrip"
    await _seed_job(db, job_id)

    await repo.upsert(
        job_id=job_id,
        apps_to_qa=["hub", "companion"],
        apps_skipped=["lane"],
        force_all_apps=False,
        changed_files=["apps/hub/app/page.tsx", "packages/types/src/index.ts"],
        base_branch="main",
        dep_graph=[
            {"source": "@x/hub", "target": "@x/types"},
            {"source": "@x/companion", "target": "@x/types"},
        ],
    )

    md = await repo.get(job_id)
    assert isinstance(md, QARunMetadata)
    assert md.job_id == job_id
    assert md.apps_to_qa == ["hub", "companion"]
    assert md.apps_skipped == ["lane"]
    assert md.force_all_apps is False
    assert md.changed_files == [
        "apps/hub/app/page.tsx",
        "packages/types/src/index.ts",
    ]
    assert md.base_branch == "main"
    assert md.dep_graph == [
        {"source": "@x/hub", "target": "@x/types"},
        {"source": "@x/companion", "target": "@x/types"},
    ]


@pytest.mark.asyncio
async def test_upsert_is_idempotent_replaces_on_conflict(repo, db):
    job_id = "test-md-idempotent"
    await _seed_job(db, job_id)

    await repo.upsert(
        job_id=job_id,
        apps_to_qa=["hub"],
        apps_skipped=["companion", "lane"],
        force_all_apps=False,
        changed_files=["apps/hub/app/page.tsx"],
        base_branch="main",
        dep_graph=[],
    )
    # Re-upsert with different values — row replaced, not duplicated.
    await repo.upsert(
        job_id=job_id,
        apps_to_qa=["hub", "companion", "lane"],
        apps_skipped=[],
        force_all_apps=True,
        changed_files=[],
        base_branch="develop",
        dep_graph=[{"source": "@x/a", "target": "@x/b"}],
    )

    md = await repo.get(job_id)
    assert md is not None
    assert md.apps_to_qa == ["hub", "companion", "lane"]
    assert md.apps_skipped == []
    assert md.force_all_apps is True
    assert md.changed_files == []
    assert md.base_branch == "develop"
    assert md.dep_graph == [{"source": "@x/a", "target": "@x/b"}]


@pytest.mark.asyncio
async def test_upsert_force_all_apps_true_with_empty_diff(repo, db):
    """qa_required=always (or override) skips diff resolution; persist anyway."""
    job_id = "test-md-force-all"
    await _seed_job(db, job_id)

    await repo.upsert(
        job_id=job_id,
        apps_to_qa=["hub", "companion"],
        apps_skipped=[],
        force_all_apps=True,
        changed_files=[],
        base_branch="",
        dep_graph=[],
    )

    md = await repo.get(job_id)
    assert md is not None
    assert md.force_all_apps is True
    assert md.changed_files == []
    assert md.base_branch == ""
    assert md.dep_graph == []


@pytest.mark.asyncio
async def test_upsert_round_trips_head_sha(repo, db):
    """Phase 5F: head_sha is persisted and read back round-trip.

    Legacy callers (no kwarg) round-trip the empty-string default so the
    flake-heatmap aggregator can filter them out.
    """
    job_with_sha = "test-md-headsha"
    await _seed_job(db, job_with_sha)
    await repo.upsert(
        job_id=job_with_sha,
        apps_to_qa=["hub"],
        apps_skipped=[],
        force_all_apps=False,
        changed_files=["apps/hub/app/page.tsx"],
        base_branch="main",
        dep_graph=[],
        head_sha="cafef00dba5e",
    )
    md = await repo.get(job_with_sha)
    assert md is not None
    assert md.head_sha == "cafef00dba5e"

    # Default (no head_sha kwarg) — round-trips as empty string.
    job_no_sha = "test-md-headsha-default"
    await _seed_job(db, job_no_sha)
    await repo.upsert(
        job_id=job_no_sha,
        apps_to_qa=["hub"],
        apps_skipped=[],
        force_all_apps=False,
        changed_files=[],
        base_branch="main",
        dep_graph=[],
    )
    md = await repo.get(job_no_sha)
    assert md is not None
    assert md.head_sha == ""
