"""Migration 160: ``jobs.account_waited_seconds``, what a job has spent parked for accounts, all its waits together.

The settings document's ``account_wait.max_total_wait_seconds`` bounds a job's waits for spent LLM accounts
together. A wait that ends in a relaunch leaves no checkpoint, so the sum is on the job's row, and the account-wait
loop adds each wait's time to it when it takes the job back (``JobsRepository.claim_account_wait``). The number is
``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS

#: This migration's number.
VERSION = 160

pytestmark = [pytest.mark.requires_postgres]


def test_the_waited_seconds_migration_is_listed_once() -> None:
    """The last-entry assertion moved to tests/unit/storage/test_migration_161.py."""
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


async def test_a_job_has_waited_nothing_until_a_wait_ends(db_with_migrations: DatabasePool) -> None:
    from embry0.storage.repositories.jobs import JobsRepository

    jobs = JobsRepository(db_with_migrations)
    job_id = await jobs.create(repo="acme/widgets", task="t")
    row = await jobs.get(job_id)
    assert row is not None and row.get("account_waited_seconds") == 0, (
        f"a new job, and every job before the migration, has waited 0 seconds: {row and row.get('account_waited_seconds')}"
    )
    comment = await db_with_migrations.fetchval(
        "SELECT col_description('jobs'::regclass, attnum) FROM pg_attribute "
        "WHERE attrelid = 'jobs'::regclass AND attname = 'account_waited_seconds'"
    )
    assert comment is not None and "max_total_wait_seconds" in comment, comment


async def test_taking_a_job_back_adds_the_time_it_waited(db_with_migrations: DatabasePool) -> None:
    from embry0.storage.repositories.jobs import JobsRepository

    jobs = JobsRepository(db_with_migrations)
    job_id = await jobs.create(repo="acme/widgets", task="t")

    async def park(since: datetime | None) -> None:
        record = {"provider": "anthropic", "reason": "quota_exhausted", "until": datetime.now(UTC).isoformat()}
        if since is not None:
            record["since"] = since.isoformat()
        await db_with_migrations.execute(
            "UPDATE jobs SET status = 'paused', account_wait = $2::jsonb WHERE job_id = $1", job_id, record
        )

    await park(datetime.now(UTC) - timedelta(hours=2))
    assert await jobs.claim_account_wait(job_id)
    first = (await jobs.get(job_id) or {}).get("account_waited_seconds")
    assert first is not None and 7200 <= first <= 7200 + 60, f"two hours parked adds two hours: {first}"
    await park(datetime.now(UTC) - timedelta(minutes=30))
    assert await jobs.claim_account_wait(job_id)
    second = (await jobs.get(job_id) or {}).get("account_waited_seconds")
    assert second is not None and first + 1800 <= second <= first + 1800 + 60, (
        f"the next wait adds to the first: {first} then {second}"
    )
    await park(None)
    assert await jobs.claim_account_wait(job_id)
    assert (await jobs.get(job_id) or {}).get("account_waited_seconds") == second, (
        "a wait parked before the migration, with no since, adds nothing"
    )


async def test_a_park_for_an_account_has_its_start_from_the_database_s_clock(db_with_migrations: DatabasePool) -> None:
    """The start the loop subtracts from its NOW() is that same clock's: set in the park's own write."""
    from embry0.storage.repositories.jobs import JobsRepository

    jobs = JobsRepository(db_with_migrations)
    job_id = await jobs.create(repo="acme/widgets", task="t")
    await jobs.update(job_id, status="running")
    wait = {"provider": "anthropic", "reason": "quota_exhausted", "until": datetime.now(UTC).isoformat()}
    await jobs.update(job_id, status="paused", account_wait=wait, count_account_wait=True)
    behind = await db_with_migrations.fetchval(
        "SELECT NOW() - (account_wait->>'since')::timestamptz FROM jobs WHERE job_id = $1", job_id
    )
    assert behind is not None and timedelta(0) <= behind < timedelta(seconds=30), (
        f"since is the database's now when the job was parked: {behind}"
    )
    assert await jobs.claim_account_wait(job_id)
    await jobs.update(job_id, status="paused", account_wait=wait)
    row = await db_with_migrations.fetchval("SELECT account_wait FROM jobs WHERE job_id = $1", job_id)
    assert "since" not in row, f"a wait that does not count (the proxies') has no start: {row}"
