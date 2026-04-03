"""Stats API — aggregated metrics compatible with frontend dashboard."""

from fastapi import APIRouter, Depends

from legion.api.deps import get_db
from legion.storage.database import DatabasePool

router = APIRouter()


@router.get("/stats")
async def get_stats(db: DatabasePool = Depends(get_db)) -> dict:
    """Return dashboard statistics in the format the frontend expects."""
    total_jobs = await db.fetchval("SELECT COUNT(*) FROM jobs") or 0
    completed = await db.fetchval("SELECT COUNT(*) FROM jobs WHERE status = 'completed'") or 0
    failed = await db.fetchval("SELECT COUNT(*) FROM jobs WHERE status = 'failed'") or 0
    total_cost = float(await db.fetchval("SELECT COALESCE(SUM(total_cost_usd), 0) FROM jobs") or 0.0)
    success_rate = completed / total_jobs if total_jobs > 0 else 0.0

    return {
        # Core metrics
        "total_issues": total_jobs,
        "total_jobs": total_jobs,
        "completed": completed,
        "failed": failed,
        "success_rate": success_rate,
        "total_cost_usd": total_cost,

        # Cost breakdowns (empty until we have tier tracking)
        "daily_cost_usd": 0.0,
        "monthly_cost_usd": 0.0,
        "cost_by_tier": {},
        "avg_cost_per_tier": {},

        # Tier breakdowns (empty until we have tier tracking)
        "success_rate_by_tier": {},
        "avg_attempts_by_tier": {},

        # Queue
        "queue_depth": 0,

        # Failure categories (empty)
        "failure_categories": {},

        # Recent issues (empty list)
        "recent_issues": [],
    }
