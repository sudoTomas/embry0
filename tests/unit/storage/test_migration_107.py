"""Migration 107 — ``traces.skill_actions``: each run's tool calls by the skill they train.

Progression (spec 2026-10-24) prices a run by what its tool calls trained.
The agent node counts them as ``kind:skill`` keys; older rows keep the
default ``{}`` and progression reads their ``tools_called`` instead.
"""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS


def test_migration_107_present_and_gapless() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(107) == 1, "migration 107 (traces.skill_actions) must exist exactly once"
    assert versions == list(range(1, len(versions) + 1))
    assert "skill_actions" in next(d for v, d, _ in MIGRATIONS if v == 107)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_old_rows_read_as_no_actions(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    column = await db.fetchrow(
        """
        SELECT data_type, is_nullable, column_default FROM information_schema.columns
        WHERE table_name = 'traces' AND column_name = 'skill_actions'
        """
    )
    assert column is not None and column["data_type"] == "jsonb" and column["is_nullable"] == "NO"
    await db.execute("INSERT INTO jobs (job_id, repo, task, status) VALUES ('job-107', 'o/r', 't', 'completed')")
    await db.execute(
        "INSERT INTO traces (trace_id, job_id, agent_type, model, result) VALUES ('trc-107', 'job-107', 'qa', 'm', 'success')"
    )
    assert await db.fetchval("SELECT skill_actions FROM traces WHERE trace_id = 'trc-107'") == {}
