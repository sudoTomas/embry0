"""Stats API."""
from fastapi import APIRouter, Depends

from legion.api.deps import get_db
from legion.storage.database import DatabasePool

router = APIRouter()


@router.get("/stats")
async def get_stats(db: DatabasePool = Depends(get_db)) -> dict:
    total_jobs = await db.fetchval("SELECT COUNT(*) FROM jobs") or 0
    completed = await db.fetchval("SELECT COUNT(*) FROM jobs WHERE status = 'completed'") or 0
    failed = await db.fetchval("SELECT COUNT(*) FROM jobs WHERE status = 'failed'") or 0
    total_cost = await db.fetchval("SELECT COALESCE(SUM(total_cost_usd), 0) FROM jobs") or 0.0
    return {
        "total_jobs": total_jobs,
        "completed": completed,
        "failed": failed,
        "success_rate": completed / total_jobs if total_jobs > 0 else 0.0,
        "total_cost_usd": float(total_cost),
    }
