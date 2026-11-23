"""Migration 155 — ``traces.pricing``: what each agent run was priced with (#173 PR 3, ADR 0031).

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS
from embry0.storage.repositories.traces import TracesRepository

#: This migration's number.
VERSION = 155


def test_the_trace_pricing_migration_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_154.py)."""
    assert MIGRATIONS[-1][0] == VERSION, f"migration {VERSION} must add traces.pricing"
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


@pytest.mark.requires_postgres
async def test_a_trace_keeps_its_pricing_and_only_as_an_object(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await db.execute("INSERT INTO jobs (job_id, task, repo) VALUES ('job-p', 't', 'acme/app')")
    traces = TracesRepository(db)
    pricing = {"provider": "xai", "rates": {"in": 2.0, "out": 6.0}, "charged_from": "catalogue"}
    priced = await traces.create(
        job_id="job-p", agent_type="developer", model="grok-4.7", result="running", pricing=pricing
    )
    unpriced = await traces.create(job_id="job-p", agent_type="developer", model="grok-4.7", result="running")
    stored = {r["trace_id"]: r["pricing"] for r in await db.fetch("SELECT trace_id, pricing FROM traces")}
    assert stored == {priced: pricing, unpriced: None}
    assert (await traces.get(priced) or {})["pricing"] == pricing, "the trace's reads carry it"
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute("UPDATE traces SET pricing = '[]'::jsonb WHERE trace_id = $1", priced)
