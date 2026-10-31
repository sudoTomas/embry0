"""What background features spend on models outside any job (#164), one row per call.

Written by :class:`embry0.services.background_models.BackgroundCalls`; read by
the budget gate's rollup (``BudgetSpendRepository``) and by ``/stats``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from embry0.storage.database import DatabasePool

# This month's rows only, the widest window anything reads (the budget gate's
# monthly cap, /stats' day and month), so the read is a range of
# idx_background_spend_at however long the table grows. It has no retention,
# like the jobs' and traces' spend beside it.
_MONTH_SQL = """
SELECT
    feature,
    COUNT(*)                                                                   AS calls,
    COALESCE(SUM(input_tokens + cache_read_tokens), 0)                         AS input_tokens,
    COALESCE(SUM(output_tokens), 0)                                            AS output_tokens,
    COALESCE(SUM(cost_usd) FILTER (WHERE at >= date_trunc('day', NOW())), 0)   AS daily_usd,
    COALESCE(SUM(cost_usd), 0)                                                 AS monthly_usd
FROM background_spend
WHERE at >= date_trunc('month', NOW())
GROUP BY feature
ORDER BY monthly_usd DESC, feature
"""


class BackgroundSpendRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def record(
        self,
        *,
        call_id: str,
        at: datetime,
        feature: str,
        model: str,
        repo: str | None,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cost_usd: float,
    ) -> None:
        """One call's tokens and price, stamped with when the call was made.

        Idempotent by ``call_id``: storing a row again after the driver raised
        on a commit that went through stores nothing, so a held row retried
        is never counted twice.
        """
        await self._db.execute(
            """
            INSERT INTO background_spend
                (call_id, at, feature, model, repo, input_tokens, output_tokens, cache_read_tokens, cost_usd)
            VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (call_id) DO NOTHING
            """,
            call_id,
            at,
            feature,
            model,
            repo or None,
            max(int(input_tokens), 0),
            max(int(output_tokens), 0),
            max(int(cache_read_tokens), 0),
            max(float(cost_usd), 0.0),
        )

    async def this_month(self) -> list[dict[str, Any]]:
        """Each feature's calls, tokens and spend this month, and its spend today, the costliest first."""
        return [
            {
                "feature": str(r["feature"]),
                "calls": int(r["calls"]),
                "input_tokens": int(r["input_tokens"]),
                "output_tokens": int(r["output_tokens"]),
                "daily_usd": float(r["daily_usd"]),
                "monthly_usd": float(r["monthly_usd"]),
            }
            for r in await self._db.fetch(_MONTH_SQL)
        ]
