"""Migration 132 — an index on jobs.pr_url, so the first_try fact reads jobs through indexes (#208).

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS

#: This migration's number.
VERSION = 132

_STATEMENT = "CREATE INDEX IF NOT EXISTS idx_jobs_pr_url ON jobs (pr_url) WHERE pr_url IS NOT NULL"


def _sql() -> str:
    return next((s for v, _, s in MIGRATIONS if v == VERSION), "")


def test_the_pr_url_index_migration_is_present_once() -> None:
    """The last-entry assertion moved to tests/unit/storage/test_migration_133.py."""
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must index jobs.pr_url, once"


def test_the_index_is_partial_plain_and_idempotent() -> None:
    """The runner wraps each migration in a transaction, where CREATE INDEX CONCURRENTLY cannot run."""
    sql = _sql()
    assert _STATEMENT in sql, f"migration {VERSION} must create the partial index on jobs.pr_url"
    assert "CONCURRENTLY" not in sql


async def _definitions(db: DatabasePool) -> list[str]:
    rows = await db.fetch("SELECT indexdef FROM pg_indexes WHERE tablename = 'jobs' AND indexname = 'idx_jobs_pr_url'")
    return [str(r["indexdef"]) for r in rows]


@pytest.mark.requires_postgres
async def test_the_index_exists_once_after_running_twice(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    expected = ["CREATE INDEX idx_jobs_pr_url ON public.jobs USING btree (pr_url) WHERE (pr_url IS NOT NULL)"]
    assert await _definitions(db) == expected, "the migrations must leave a partial index on jobs.pr_url"
    await db.execute(_sql())
    assert await _definitions(db) == expected, "running the migration again must keep the one index"


#: 5,000 jobs, half of them merged with a pull request and none on an issue. Statistics
#: on a table this size price a scan of jobs per merged job above a lookup per pull request;
#: on a few dozen rows a scan is honestly cheaper, so the plan would say nothing.
_SEED = """
INSERT INTO jobs (job_id, repo, task, status, delivery_state, pr_url, created_at)
SELECT 'job-' || g, 'acme/widgets', 't',
       CASE WHEN g % 2 = 0 THEN 'pr_merged' ELSE 'failed' END,
       CASE WHEN g % 2 = 0 THEN 'merged' ELSE 'none' END,
       CASE WHEN g % 2 = 0 THEN 'https://forge.example.com/acme/widgets/pull/' || g END,
       TIMESTAMPTZ '2026-01-01 00:00Z' + g * INTERVAL '1 minute'
FROM generate_series(1, 5000) g
"""


@pytest.mark.requires_postgres
async def test_first_try_looks_up_earlier_jobs_of_a_pull_request_through_the_index(
    db_with_migrations: DatabasePool,
) -> None:
    from embry0.services.progression_pack import default_rules
    from embry0.storage.repositories.progression import facts_sql

    db = db_with_migrations
    await db.execute(_SEED)
    await db.execute("ANALYZE jobs")
    rules = default_rules()
    first_try = [q for q in rules.fact_queries if q.fragment == "first_try"]
    assert first_try, "the shipped pack counts the first_try metric"
    sql, args = facts_sql(first_try, rules.pack.timezone)
    plan = "\n".join(str(r[0]) for r in await db.fetch("EXPLAIN " + sql, *args))
    assert "idx_jobs_pr_url" in plan, (
        f"first_try does not find a pull request's earlier jobs through idx_jobs_pr_url:\n{plan}"
    )
