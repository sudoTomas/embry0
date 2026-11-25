"""Migration 157: ``local_model_leases.server``, the local model server each lease holds a slot of (#174).

Each server has its own cap and its own lock: a server's runs never wait on another's, and a lease is
written with its server or not at all. The number is ``VERSION`` alone: renumbering the migration changes
that line and this file's name.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS
from embry0.storage.repositories.local_model_leases import LocalModelLeasesRepository

#: This migration's number.
VERSION = 157

_ARGS = {"job_id": "job-1", "agent_type": "analysis", "model": "custom/qwen", "run_seconds": 600}


def _leases(db: DatabasePool) -> LocalModelLeasesRepository:
    leases = LocalModelLeasesRepository(db)
    assert "server" in inspect.signature(leases.try_take).parameters, (
        "a lease names no server: every server's runs share one cap and one lock"
    )
    return leases


def test_the_lease_server_migration_is_listed_once() -> None:
    """The last-entry assertion passed to tests/unit/storage/test_migration_158.py."""
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


@pytest.mark.requires_postgres
async def test_each_server_has_its_own_cap(db_with_migrations: DatabasePool) -> None:
    leases = _leases(db_with_migrations)
    now = datetime.now(UTC)
    first = await leases.try_take(cap=1, server="cred-a", now=now, **_ARGS)
    assert first is not None
    assert await leases.try_take(cap=1, server="cred-a", now=now, **_ARGS) is None, "cred-a's one slot is held"
    other = await leases.try_take(cap=1, server="cred-b", now=now, **_ARGS)
    assert other is not None, "another server's slot is its own: a run there never waits on cred-a's"
    held = {(row["lease_id"], row["server"]) for row in await leases.live(now)}
    assert held == {(first, "cred-a"), (other, "cred-b")}, held


@pytest.mark.requires_postgres
async def test_a_lease_is_written_with_its_server_or_not_at_all(db_with_migrations: DatabasePool) -> None:
    try:
        await db_with_migrations.execute(
            "INSERT INTO local_model_leases (lease_id, expires_at) VALUES ('lease-x', now() + interval '1 hour')"
        )
    except asyncpg.NotNullViolationError:
        return
    pytest.fail("a lease was written without its server, so no server's cap counts it")


@pytest.mark.requires_postgres
async def test_racing_takes_hold_each_server_to_its_cap(db_with_migrations: DatabasePool) -> None:
    leases = _leases(db_with_migrations)
    now = datetime.now(UTC)
    taken = await asyncio.gather(
        *(leases.try_take(cap=2, server=server, now=now, **_ARGS) for server in ("cred-a", "cred-b") for _ in range(6))
    )
    rows = await leases.live(now)
    per_server = {server: sum(1 for row in rows if row["server"] == server) for server in ("cred-a", "cred-b")}
    assert per_server == {"cred-a": 2, "cred-b": 2}, f"each server's lock holds it to its cap of two: {per_server}"
    assert sum(lease is not None for lease in taken) == 4


@pytest.mark.requires_postgres
async def test_each_server_is_taken_under_a_lock_of_its_own(db_with_migrations: DatabasePool) -> None:
    """A take waits only for its own server's lock: one server's busy moment never holds up another's runs."""
    from embry0.storage.repositories import local_model_leases

    leases = _leases(db_with_migrations)
    now = datetime.now(UTC)
    held = f"{local_model_leases._LOCK_KEY}:cred-a"
    async with db_with_migrations.transaction() as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", held)
        other = await asyncio.wait_for(leases.try_take(cap=1, server="cred-b", now=now, **_ARGS), timeout=10)
        assert other is not None, "cred-b's take does not wait for cred-a's lock"
        same = asyncio.create_task(leases.try_take(cap=1, server="cred-a", now=now, **_ARGS))
        await asyncio.sleep(0.5)
        assert not same.done(), "cred-a's take waits for cred-a's lock"
    assert await asyncio.wait_for(same, timeout=10) is not None
