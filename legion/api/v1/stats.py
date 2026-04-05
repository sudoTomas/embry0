"""Stats API — aggregated metrics compatible with frontend dashboard."""

from fastapi import APIRouter, Depends

from legion.api.deps import get_db
from legion.storage.database import DatabasePool

router = APIRouter()


@router.get("/stats")
async def get_stats(db: DatabasePool = Depends(get_db)) -> dict:
    row = await db.fetchrow(
        """
        SELECT
            COUNT(*) AS total_jobs,
            COUNT(*) FILTER (WHERE status = 'completed') AS completed,
            COUNT(*) FILTER (WHERE status = 'failed') AS failed,
            COALESCE(SUM(total_cost_usd), 0) AS total_cost
        FROM jobs
        """
    )
    total_jobs = row["total_jobs"] if row else 0
    completed = row["completed"] if row else 0
    failed = row["failed"] if row else 0
    total_cost = float(row["total_cost"] if row else 0.0)
    success_rate = completed / total_jobs if total_jobs > 0 else 0.0
    total_issues = await db.fetchval("SELECT COUNT(*) FROM issues") or 0

    return {
        "total_issues": total_issues,
        "total_jobs": total_jobs,
        "completed": completed,
        "failed": failed,
        "success_rate": success_rate,
        "total_cost_usd": total_cost,
        "daily_cost_usd": 0.0,
        "monthly_cost_usd": 0.0,
        "cost_by_tier": {},
        "avg_cost_per_tier": {},
        "success_rate_by_tier": {},
        "avg_attempts_by_tier": {},
        "queue_depth": 0,
        "failure_categories": {},
        "recent_issues": [],
    }
