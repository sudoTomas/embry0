"""The ``budget_config`` row: where the fleet budget lived before the settings document.

Until the settings document's ``budget`` section (:mod:`embry0.settings.sections.budget`),
the per-job ceiling, the daily and monthly caps, the per-person rate limit
and the overrun mode were one row here, edited with ``PUT /config/budget``.
Its environment variables were the defaults until someone saved the row, and
then the row won for good.

:func:`read_row` reads that row once, at the first start of the release that
moved the five keys, inside the settings import's transaction
(:class:`embry0.settings.bootstrap.EarlierSource`): each value as the budget's
readers read it, so a deployment keeps the caps it had. Without a row, the
import reads the variables instead. Nothing else reads or writes the table
now; it is kept so that a rollback to the release before finds the row as it
was at the move.
"""

from __future__ import annotations

import math
from typing import Any, Final

#: How the settings import names this table in the comment above what it imported.
LABEL: Final = "the budget_config table"

_ROW_SQL: Final = (
    "SELECT max_budget_per_job_usd, daily_cap_usd, monthly_cap_usd, rate_limit_per_author_per_hour, overrun_mode "
    "FROM budget_config WHERE id = 'default'"
)


def _positive(value: Any) -> float:
    """A cap as the dispatch gate and the agent node read one: a positive, finite number, else 0, no cap."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


async def read_row(conn: Any, config: Any) -> dict[str, Any] | None:
    """The saved row's five values as their readers read them, or None when no row was ever saved.

    The dispatch gate read the daily and monthly caps and the rate limit
    straight from the row, 0 or less (or not finite) being no limit. The
    agent node read the ceiling from the row, and a ceiling at or below 0
    there as ``MAX_BUDGET_USD``'s; the overrun mode from the row, an empty one
    as ``BUDGET_OVERRUN_MODE``'s, and any word but soft or hard as soft. The
    API never saved a 0, an infinite value or a bad word: only SQL could.

    One reading is tighter than the old one: a ceiling at or below 0, **or
    not finite**, reads as ``MAX_BUDGET_USD``'s. The old reader took an
    infinite ceiling as no ceiling; the document cannot hold one, and a cap
    is never loosened by the move, so it takes the variable's (10 USD by
    default) instead.
    """
    row = await conn.fetchrow(_ROW_SQL)
    if row is None:
        return None
    ceiling = _positive(row["max_budget_per_job_usd"]) or _positive(getattr(config, "max_budget_usd", 0.0))
    mode = row["overrun_mode"] or getattr(config, "budget_overrun_mode", "soft")
    try:
        limit = int(row["rate_limit_per_author_per_hour"] or 0)
    except (TypeError, ValueError):
        limit = 0
    return {
        "max_budget_per_job_usd": ceiling,
        "daily_cap_usd": _positive(row["daily_cap_usd"]),
        "monthly_cap_usd": _positive(row["monthly_cap_usd"]),
        "rate_limit_per_author_per_hour": max(limit, 0),
        "overrun_mode": mode if mode in ("soft", "hard") else "soft",
    }
