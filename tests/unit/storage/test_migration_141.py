"""Migration 141 — ``jobs.repo`` of a job posted with a git context alone.

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS, run_migrations

#: This migration's number.
VERSION = 141

#: When each row was last changed, long before the migration runs.
CHANGED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

#: Each job: its stored repo, its context, and the repo it names once the migration ran.
ROWS: dict[str, tuple[str | None, dict[str, Any] | None, str | None]] = {
    "job-git-context": (None, {"type": "git", "repo": "acme/widgets"}, "acme/widgets"),
    "job-named": ("acme/widgets", {"type": "git", "repo": "acme/widgets"}, "acme/widgets"),
    "job-named-otherwise": ("acme/renamed", {"type": "git", "repo": "acme/widgets"}, "acme/renamed"),
    "job-over-a-url": (None, {"type": "http", "url": "https://example.com/report"}, None),
    "job-no-repository": (None, {"type": "none"}, None),
    "job-no-repository-naming-one": (None, {"type": "none", "repo": "acme/widgets"}, None),
    "job-empty-git-context": (None, {"type": "git", "repo": ""}, None),
    "job-no-context": (None, None, None),
}


def test_the_job_repository_migration_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_140.py)."""
    assert MIGRATIONS[-1][0] == VERSION, f"migration {VERSION} must fill jobs.repo from a git context"
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


async def _rows(db: DatabasePool) -> dict[str, tuple[str | None, datetime]]:
    rows = await db.fetch("SELECT job_id, repo, updated_at FROM jobs WHERE job_id = ANY($1::text[])", list(ROWS))
    return {str(r["job_id"]): (r["repo"], r["updated_at"]) for r in rows}


@pytest.mark.requires_postgres
async def test_a_job_posted_with_a_git_context_alone_names_its_repository_once_and_for_all(
    db_with_migrations: DatabasePool,
) -> None:
    """Rows written before the migration: a git context's repository fills a repo the row lacks, nothing else
    changes, not even when the row last changed, and a second run changes nothing."""
    db = db_with_migrations
    await db.execute("DELETE FROM embry0_migrations WHERE version = $1", VERSION)
    for job, (repo, context, _after) in ROWS.items():
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, context, created_at, updated_at) VALUES ($1, $2, 'Task', $3, $4, $4)",
            job,
            repo,
            context,
            CHANGED,
        )
    await run_migrations(db)
    once = await _rows(db)
    assert {job: repo for job, (repo, _at) in once.items()} == {job: after for job, (*_, after) in ROWS.items()}, (
        f"migration {VERSION} names the repository of a job with a git context alone, and no other: {once}"
    )
    assert {at for _repo, at in once.values()} == {CHANGED}, f"no row's updated_at moves: {once}"
    left = await db.fetchval(
        "SELECT count(*) FROM jobs WHERE repo IS NULL AND context->>'type' = 'git' AND COALESCE(context->>'repo', '') <> ''"
    )
    assert left == 0, f"no job with a git context is left without its repository: {left}"

    await db.execute("DELETE FROM embry0_migrations WHERE version = $1", VERSION)
    await run_migrations(db)
    assert await _rows(db) == once, "running the migration again changes nothing"
