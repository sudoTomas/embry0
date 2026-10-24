"""Spend rollups for the pre-dispatch budget gate.

Kept out of the API layer on purpose: ``/stats`` renders the same daily and
monthly numbers, but the gate has to read them from the orchestrator process
too. One query shape, one owner.
"""

from __future__ import annotations

from datetime import datetime

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.gate_refusal import gate_refusal_sql

# GREATEST over the trace rollup matters for in-flight jobs: ``jobs.
# total_cost_usd`` is only written when a job reaches a terminal state, so a
# burst of running jobs would otherwise read as zero spend and sail past the
# cap. Traces land per agent turn, so they carry the accruing cost.
_ROLLUP_SQL = """
SELECT
    COALESCE(SUM(c.cost) FILTER (WHERE c.ts >= date_trunc('day', NOW())), 0)   AS daily,
    COALESCE(SUM(c.cost) FILTER (WHERE c.ts >= date_trunc('month', NOW())), 0) AS monthly
FROM (
    SELECT
        COALESCE(j.started_at, j.created_at) AS ts,
        GREATEST(j.total_cost_usd, COALESCE(t.trace_cost, 0.0)) AS cost
    FROM jobs j
    LEFT JOIN (
        SELECT job_id, SUM(cost_usd) AS trace_cost FROM traces GROUP BY job_id
    ) t ON t.job_id = j.job_id
) c
"""

# Buckets on ``jobs.dispatched_by`` — the actor that asked for the job — NOT
# on ``issues.created_by``, which is a provenance constant ("webhook-label",
# "linear-webhook", "triage") and would make this one fleet-wide throttle.
#
# Jobs the gate itself already refused are excluded: counting a refusal toward
# the next hour's budget lets one lockout extend itself indefinitely. ONLY
# those: a job that ran and was hard-stopped in-run spent money and counts —
# excluding it too let an actor pin ``budget_overrun_mode: "hard"`` with a tiny
# budget and start jobs that never counted (#331). ``gate_refusal_sql`` is the
# one definition of a refusal, shared with ``JobsRepository.count_dispatched_by``.
_ACTOR_COUNT_SQL = f"""
SELECT COUNT(*)
FROM jobs
WHERE dispatched_by = $1
  AND created_at >= $2
  AND NOT {gate_refusal_sql("jobs")}
  AND job_id IS DISTINCT FROM $3
"""


class BudgetSpendRepository:
    """Read-only aggregates over ``jobs`` and ``traces``."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def spend_rollup(self) -> dict[str, float]:
        """Spend since midnight and since the start of the month, in USD."""
        row = await self._db.fetchrow(_ROLLUP_SQL)
        if not row:
            return {"daily": 0.0, "monthly": 0.0}
        return {"daily": float(row["daily"] or 0.0), "monthly": float(row["monthly"] or 0.0)}

    async def dispatch_count_since(self, actor: str, since: datetime, *, excluding: str | None = None) -> int:
        """Jobs ``actor`` dispatched since ``since``, excluding refused ones and the job ``excluding`` names.

        ``excluding`` is the job the runner is about to start (#116): its row
        already exists, and counting it made the job count against itself.
        """
        count = await self._db.fetchval(_ACTOR_COUNT_SQL, actor, since, excluding)
        return int(count or 0)
