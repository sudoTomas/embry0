"""The ``budget_config`` row as the settings import reads it once (#171): each value as its reader read it.

Before the settings document's ``budget`` section, the dispatch gate read the
daily and monthly caps and the rate limit from the row, a value at or below 0
being no limit; the agent node read the ceiling from the row, one at or below
0 as ``MAX_BUDGET_USD``'s, and the overrun mode from the row, an empty one as
``BUDGET_OVERRUN_MODE``'s and a word but soft or hard as soft.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories import budget_config

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

CONFIG = SimpleNamespace(max_budget_usd=6.0, budget_overrun_mode="hard")


async def _row(db: DatabasePool, **columns: Any) -> None:
    names = ", ".join(["id", *columns])
    marks = ", ".join(f"${i}" for i in range(1, len(columns) + 2))
    await db.execute(f"INSERT INTO budget_config ({names}) VALUES ({marks})", "default", *columns.values())


async def _read(db: DatabasePool, config: Any = CONFIG) -> dict[str, Any] | None:
    read_row = getattr(budget_config, "read_row", None)
    assert read_row is not None, "the settings import reads the saved row through budget_config.read_row"
    async with db.transaction() as conn:
        return await read_row(conn, config)


async def test_no_saved_row_is_nothing_to_read(db_with_migrations: DatabasePool) -> None:
    assert await _read(db_with_migrations) is None, "without a row the import reads the variables instead"


async def test_a_saved_row_reads_as_its_values(db_with_migrations: DatabasePool) -> None:
    await _row(
        db_with_migrations,
        max_budget_per_job_usd=25.0,
        daily_cap_usd=40.0,
        monthly_cap_usd=900.0,
        rate_limit_per_author_per_hour=2,
        overrun_mode="soft",
    )
    assert await _read(db_with_migrations) == {
        "max_budget_per_job_usd": 25.0,
        "daily_cap_usd": 40.0,
        "monthly_cap_usd": 900.0,
        "rate_limit_per_author_per_hour": 2,
        "overrun_mode": "soft",
    }


async def test_a_row_saved_with_one_field_holds_the_column_defaults_for_the_rest(
    db_with_migrations: DatabasePool,
) -> None:
    """PUT /config/budget created the row with the table's own defaults, not the variables, and the readers
    read those from then on."""
    await _row(db_with_migrations, daily_cap_usd=40.0)
    assert await _read(db_with_migrations, SimpleNamespace(max_budget_usd=99.0, budget_overrun_mode="hard")) == {
        "max_budget_per_job_usd": 10.0,
        "daily_cap_usd": 40.0,
        "monthly_cap_usd": 500.0,
        "rate_limit_per_author_per_hour": 5,
        "overrun_mode": "soft",
    }


async def test_values_no_api_could_save_read_as_their_readers_read_them(db_with_migrations: DatabasePool) -> None:
    """Only SQL could store these: a cap at or below 0 or not finite was no limit, a ceiling at or below 0 was
    MAX_BUDGET_USD's, an empty mode BUDGET_OVERRUN_MODE's."""
    await _row(
        db_with_migrations,
        max_budget_per_job_usd=0.0,
        daily_cap_usd=-5.0,
        monthly_cap_usd=float("inf"),
        rate_limit_per_author_per_hour=-1,
        overrun_mode="",
    )
    assert await _read(db_with_migrations) == {
        "max_budget_per_job_usd": 6.0,
        "daily_cap_usd": 0.0,
        "monthly_cap_usd": 0.0,
        "rate_limit_per_author_per_hour": 0,
        "overrun_mode": "hard",
    }


async def test_a_mode_but_soft_or_hard_reads_as_soft(db_with_migrations: DatabasePool) -> None:
    await _row(db_with_migrations, overrun_mode="HARD")
    found = await _read(db_with_migrations)
    assert found is not None and found["overrun_mode"] == "soft"


async def test_a_ceiling_of_zero_with_no_variable_is_no_ceiling(db_with_migrations: DatabasePool) -> None:
    await _row(db_with_migrations, max_budget_per_job_usd=0.0)
    found = await _read(db_with_migrations, SimpleNamespace(max_budget_usd=0.0, budget_overrun_mode="soft"))
    assert found is not None and found["max_budget_per_job_usd"] == 0.0


async def test_an_infinite_saved_ceiling_reads_as_the_variables_not_as_no_ceiling(
    db_with_migrations: DatabasePool,
) -> None:
    """Pinned on purpose: the old reader took an infinite ceiling as no ceiling. The document holds no
    infinite value and a move never loosens a cap, so it reads as MAX_BUDGET_USD's (10 USD by default)."""
    await _row(db_with_migrations, max_budget_per_job_usd=float("inf"))
    found = await _read(db_with_migrations, SimpleNamespace(max_budget_usd=10.0, budget_overrun_mode="soft"))
    assert found is not None and found["max_budget_per_job_usd"] == 10.0
