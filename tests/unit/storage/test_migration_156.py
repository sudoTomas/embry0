"""Migration 156 — ``run_meter``, ``traces.metering`` and ``traces.sandbox_cost_usd``: metering at the proxies.

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

from datetime import timedelta

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS
from embry0.storage.repositories.run_meter import RunMeterRepository
from embry0.storage.repositories.traces import TracesRepository

#: This migration's number.
VERSION = 156


def test_the_run_meter_migration_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_155.py)."""
    assert MIGRATIONS[-1][0] == VERSION, f"migration {VERSION} must add run_meter and the trace's metering"
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


def _tally(run_id: str | None, requests: int, sandbox: str = "sbx-1", started: float = 1.0) -> dict:
    return {
        "run_id": run_id,
        "sandbox_id": sandbox,
        "started": started,
        "errors": 0,
        "open": [],
        "models": {"grok-4.7": {"input_tokens": 100 * requests, "output_tokens": 10, "requests": requests}},
    }


@pytest.mark.requires_postgres
async def test_a_read_replaces_the_tally_it_read_before_but_never_a_final_one(db_with_migrations: DatabasePool) -> None:
    repo = RunMeterRepository(db_with_migrations)
    await repo.record("xai-proxy", "b1", [_tally("run-1", 1)])
    await repo.record("xai-proxy", "b1", [_tally("run-1", 2)])
    (row,) = await repo.for_run("run-1")
    assert (row["snapshot"]["models"]["grok-4.7"]["requests"], row["final"]) == (2, False), "the newer read lost"
    await repo.record("xai-proxy", "b1", [_tally("run-1", 3)], final=True)
    await repo.record("xai-proxy", "b1", [_tally("run-1", 1)])  # a read that raced the run's end
    (row,) = await repo.for_run("run-1")
    assert (row["snapshot"]["models"]["grok-4.7"]["requests"], row["final"]) == (3, True), "a read replaced the final"


@pytest.mark.requires_postgres
async def test_each_proxy_process_s_tally_and_each_sandbox_s_calls_under_no_run_are_kept_apart(
    db_with_migrations: DatabasePool,
) -> None:
    repo = RunMeterRepository(db_with_migrations)
    await repo.record("xai-proxy", "b1", [_tally("run-1", 5), _tally(None, 2), _tally(None, 4, started=9.0)])
    await repo.record("xai-proxy", "b2", [_tally("run-1", 1)], final=True)
    await repo.record("local-llm-proxy", "l1", [_tally("run-1", 7)])
    rows = await repo.for_run("run-1")
    assert sorted((r["proxy"], r["boot_id"], r["final"]) for r in rows) == [
        ("local-llm-proxy", "l1", False),
        ("xai-proxy", "b1", False),
        ("xai-proxy", "b2", True),
    ]
    keys = await db_with_migrations.fetch("SELECT tally_key FROM run_meter WHERE run_id IS NULL ORDER BY tally_key")
    assert [k["tally_key"] for k in keys] == ["unattributed:sbx-1:1.0", "unattributed:sbx-1:9.0"]


@pytest.mark.requires_postgres
async def test_tallies_last_read_past_the_retention_are_pruned(db_with_migrations: DatabasePool) -> None:
    repo = RunMeterRepository(db_with_migrations)
    await repo.record("xai-proxy", "b1", [_tally("run-old", 1), _tally("run-new", 1)])
    await db_with_migrations.execute(
        "UPDATE run_meter SET read_at = now() - interval '31 days' WHERE run_id = 'run-old'"
    )
    assert await repo.prune(timedelta(days=30)) == 1
    assert await repo.for_run("run-old") == [] and len(await repo.for_run("run-new")) == 1


@pytest.mark.requires_postgres
async def test_a_trace_keeps_how_its_charge_was_reached_and_the_sandbox_s_figure(
    db_with_migrations: DatabasePool,
) -> None:
    db = db_with_migrations
    await db.execute("INSERT INTO jobs (job_id, task, repo) VALUES ('job-m', 't', 'acme/app')")
    traces = TracesRepository(db)
    metering = {"source": "proxy", "proxy_cost_usd": 0.02, "sandbox_cost_usd": 0.01, "gap": False}
    run = await traces.create(job_id="job-m", agent_type="developer", model="grok-4.7", result="running")
    await traces.finish(
        run,
        result="success",
        cost_usd=0.02,
        duration_ms=1,
        tools_called={},
        result_summary="",
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        error_code=None,
        stderr_tail=None,
        metering=metering,
        sandbox_cost_usd=0.01,
    )
    created = await traces.create(
        job_id="job-m",
        agent_type="developer",
        model="grok-4.7",
        result="error",
        metering=metering,
        sandbox_cost_usd=0.0,
    )
    rows = {
        r["trace_id"]: r for r in await db.fetch("SELECT trace_id, metering, sandbox_cost_usd, cost_usd FROM traces")
    }
    assert (rows[run]["metering"], rows[run]["sandbox_cost_usd"], rows[run]["cost_usd"]) == (metering, 0.01, 0.02)
    assert (rows[created]["metering"], rows[created]["sandbox_cost_usd"]) == (metering, 0.0)
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute("UPDATE traces SET metering = '[]'::jsonb WHERE trace_id = $1", run)
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(
            "INSERT INTO run_meter (proxy, boot_id, tally_key, sandbox_id, snapshot) VALUES ('p', 'b', 'k', 's', '[]')"
        )
