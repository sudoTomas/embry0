"""Stats API."""
from fastapi import APIRouter, Depends

from legion.api.deps import get_jobs_repo
from legion.storage.repositories.jobs import JobsRepository

router = APIRouter()


@router.get("/stats")
async def get_stats(jobs: JobsRepository = Depends(get_jobs_repo)) -> dict:
    _, total_jobs = await jobs.list(limit=0, offset=0)
    completed_rows, completed = await jobs.list(status="completed", limit=0, offset=0)
    failed_rows, failed = await jobs.list(status="failed", limit=0, offset=0)
    return {
        "total_jobs": total_jobs,
        "completed": completed,
        "failed": failed,
        "success_rate": completed / total_jobs if total_jobs > 0 else 0.0,
        "total_cost_usd": 0.0,
    }
