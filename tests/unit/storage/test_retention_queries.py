"""The two reads the QA image retention sweep (ADR 0017) takes its keep decisions from."""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.jobs import RELEASED_JOB_STATUSES, JobsRepository
from embry0.storage.repositories.qa_image_tags import QAImageTagsRepository
from embry0.storage.schemas import JobStatus


def test_every_status_outside_the_released_set_reads_as_live() -> None:
    live = {str(s) for s in JobStatus} - RELEASED_JOB_STATUSES
    assert live == {"pending", "running", "awaiting_input", "paused"}, (
        "a status was added to JobStatus: decide whether it releases a job's logs, sessions and volumes"
    )


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_live_job_repos_names_the_repos_with_a_job_that_has_not_ended(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    jobs = JobsRepository(db)
    for repo, status in (
        ("acme/paused", "paused"),
        ("acme/asking", "awaiting_input"),
        ("acme/pending", "pending"),
        ("acme/done", "completed"),
        ("acme/merged", "pr_merged"),
    ):
        job_id = await jobs.create(repo=repo, task="t")
        await db.execute("UPDATE jobs SET status = $2 WHERE job_id = $1", job_id, status)

    assert await jobs.live_job_repos() == {"acme/paused", "acme/asking", "acme/pending"}


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_list_all_returns_inactive_rows_too(db_with_migrations: DatabasePool) -> None:
    repo = QAImageTagsRepository(db_with_migrations)
    await repo.record(repo="acme/w", image_tag="embry0-qa-acme_w:t1@sha256:a", lockfile_sha="l1", built_by="cli")
    await repo.record(repo="acme/w", image_tag="embry0-qa-acme_w:t2@sha256:b", lockfile_sha="l2", built_by="cli")

    rows = await repo.list_all()

    assert [(r.image_tag, r.is_active) for r in rows] == [
        ("embry0-qa-acme_w:t1@sha256:a", False),
        ("embry0-qa-acme_w:t2@sha256:b", True),
    ]
