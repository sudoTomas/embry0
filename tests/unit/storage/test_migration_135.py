"""Migration 135 — what each background model call outside a job spent, by feature (#164).

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS

#: This migration's number.
VERSION = 135


def _sql() -> str:
    return next((s for v, _, s in MIGRATIONS if v == VERSION), "")


def test_the_background_spend_migration_is_listed_once() -> None:
    """The "last entry" assertion passed to tests/unit/storage/test_migration_136.py."""
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


@pytest.mark.requires_postgres
async def test_a_call_is_recorded_and_read_back_by_feature(db_with_migrations: DatabasePool) -> None:
    assert _sql(), f"migration {VERSION} creates background_spend"
    import inspect

    from embry0.storage.repositories.background_spend import BackgroundSpendRepository

    assert "call_id" in inspect.signature(BackgroundSpendRepository.record).parameters, (
        "each row carries its call's own id, so storing it again stores nothing (#287)"
    )
    spend = BackgroundSpendRepository(db_with_migrations)
    for n, cost in enumerate((0.25, 0.5)):
        await spend.record(
            call_id=f"00000000-0000-0000-0000-00000000000{n}",
            at=datetime.now(UTC),
            feature="proposal_brief",
            model="gpt-6-luna",
            repo="acme/widgets",
            input_tokens=1_000,
            output_tokens=100,
            cache_read_tokens=10,
            cost_usd=cost,
        )
    await spend.record(
        call_id="00000000-0000-0000-0000-000000000009",
        at=datetime.now(UTC),
        feature="slop_judge",
        model="custom/qwen",
        repo=None,
        input_tokens=5,
        output_tokens=1,
        cache_read_tokens=0,
        cost_usd=0.0,
    )
    rows = await spend.this_month()
    assert [(r["feature"], r["calls"], r["input_tokens"], r["output_tokens"]) for r in rows] == [
        ("proposal_brief", 2, 2_020, 200),
        ("slop_judge", 1, 5, 1),
    ], "the costliest feature first; cached input counts as input"
    assert rows[0]["monthly_usd"] == pytest.approx(0.75) and rows[0]["daily_usd"] == pytest.approx(0.75)


@pytest.mark.requires_postgres
async def test_a_negative_count_or_cost_is_refused_and_running_it_again_keeps_the_rows(
    db_with_migrations: DatabasePool,
) -> None:
    assert _sql(), f"migration {VERSION} creates background_spend"
    db = db_with_migrations
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute("INSERT INTO background_spend (feature, model, cost_usd) VALUES ('job_brief', 'm', -1)")
    await db.execute("INSERT INTO background_spend (feature, model, cost_usd) VALUES ('job_brief', 'm', 1)")
    await db.execute(_sql())
    assert await db.fetchval("SELECT count(*) FROM background_spend") == 1, "the migration is idempotent"


@pytest.mark.requires_postgres
async def test_a_row_stored_twice_under_its_call_id_counts_once_and_keeps_its_call_time(
    db_with_migrations: DatabasePool,
) -> None:
    """A held row stored again after a driver error that hid its commit is not counted twice (#287)."""
    assert _sql(), f"migration {VERSION} creates background_spend"
    import inspect

    from embry0.storage.repositories.background_spend import BackgroundSpendRepository

    assert "call_id" in inspect.signature(BackgroundSpendRepository.record).parameters, (
        "each row carries its call's own id, so storing it again stores nothing (#287)"
    )
    spend = BackgroundSpendRepository(db_with_migrations)
    called = datetime.now(UTC) - timedelta(minutes=5)
    row = {
        "call_id": "00000000-0000-0000-0000-000000000042",
        "at": called,
        "feature": "job_brief",
        "model": "gpt-6-luna",
        "repo": "acme/widgets",
        "input_tokens": 10,
        "output_tokens": 1,
        "cache_read_tokens": 0,
        "cost_usd": 0.25,
    }
    await spend.record(**row)
    await spend.record(**row)
    stored = await db_with_migrations.fetch("SELECT at, cost_usd FROM background_spend")
    assert [(r["at"], r["cost_usd"]) for r in stored] == [(called, 0.25)], "one row, dated when its call was made"
