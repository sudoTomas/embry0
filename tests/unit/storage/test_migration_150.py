"""Migration 150 — an index on the repository a job works on, which every read narrowed to a key's repositories
compares.

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS, run_migrations
from embry0.storage.repositories.traces import TracesRepository

#: This migration's number.
VERSION = 150

#: Jobs and runs enough that walking every run newest first costs what a key with few, old runs must not pay.
JOBS, RUNS = 4000, 40000


def test_the_job_repository_index_migration_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_148.py)."""
    assert MIGRATIONS[-1][0] == VERSION, f"migration {VERSION} must add the index on a job's repository"
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


def _nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [plan, *(node for child in plan.get("Plans", []) for node in _nodes(child))]


@pytest.mark.requires_postgres
async def test_a_key_s_page_of_old_runs_finds_its_jobs_first(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The runs of a key whose repository has few jobs, all older than everyone else's: its page is read from its
    jobs, through the index, not by walking every run newest first until it finds them."""
    db = db_with_migrations
    await db.execute(
        f"""INSERT INTO jobs (job_id, repo, context, task, status, created_at)
        SELECT 'j' || g, CASE WHEN g > {JOBS} - 20 AND g % 2 = 0 THEN 'acme/web' WHEN g > {JOBS} - 20 THEN NULL
                              ELSE 'org' || (g % 50) || '/r' END,
               CASE WHEN g > {JOBS} - 20 AND g % 2 = 1 THEN '{{"type": "git", "repo": "Acme/Web"}}'::jsonb END,
               't', 'completed', NOW() - (g || ' minutes')::interval
        FROM generate_series(1, {JOBS}) g"""
    )
    await db.execute(
        f"""INSERT INTO traces (trace_id, job_id, agent_type, model, result, cost_usd, created_at)
        SELECT 't' || g, 'j' || (1 + (g - 1) / {RUNS // JOBS}), 'developer', 'm', 'success', 0.01,
               NOW() - (g || ' seconds')::interval
        FROM generate_series(1, {RUNS}) g"""
    )
    await db.execute("ANALYZE jobs; ANALYZE traces")
    captured: list[tuple[str, tuple[Any, ...]]] = []
    for name in ("fetch", "fetchval"):
        real = getattr(db, name)

        async def recording(query: str, *args: Any, _real: Any = real, **kwargs: Any) -> Any:
            captured.append((query, args))
            return await _real(query, *args, **kwargs)

        monkeypatch.setattr(db, name, recording)
    rows, total = await TracesRepository(db).list_all(limit=50, repos=["acme/web"])
    monkeypatch.undo()
    assert total == 20 * (RUNS // JOBS) and len(rows) == 50, (total, len(rows))
    for query, args in captured:
        raw = await db.fetchval("EXPLAIN (ANALYZE, FORMAT JSON) " + query, *args)
        plan = (json.loads(raw) if isinstance(raw, str) else raw)[0]["Plan"]
        indexes = {node.get("Index Name") for node in _nodes(plan)}
        walked = max(int(node.get("Actual Rows", 0)) for node in _nodes(plan) if node.get("Relation Name") == "traces")
        assert "idx_jobs_repository" in indexes, f"{' '.join(query.split())[:90]}: no index on a job's repository"
        assert walked <= 20 * (RUNS // JOBS), f"the read walked {walked} runs for a page of the key's 200"


@pytest.mark.requires_postgres
@pytest.mark.changes_schema
async def test_the_index_is_built_on_upgrade(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await db.execute("DROP INDEX idx_jobs_repository")
    await db.execute("DELETE FROM embry0_migrations WHERE version = $1", VERSION)
    await run_migrations(db)
    definition = await db.fetchval("SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_jobs_repository'")
    assert definition is not None and "lower(COALESCE(repo, (context ->> 'repo'::text)))" in definition, definition
