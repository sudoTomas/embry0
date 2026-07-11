"""Queue API — real per-status counts for active jobs."""

from fastapi import APIRouter, Depends

from embry0.api.deps import get_jobs_repo
from embry0.storage.repositories.jobs import JobsRepository

router = APIRouter()


@router.get("/queue")
async def get_queue(jobs: JobsRepository = Depends(get_jobs_repo)) -> dict[str, int]:
    """Active-job counts grouped by status.

    Response includes the four non-terminal statuses; `depth` is an alias
    for `pending` retained for backward compatibility with the dashboard.
    """
    summary = await jobs.queue_summary()
    return {
        "depth": summary["pending"],  # back-compat
        "pending": summary["pending"],
        "running": summary["running"],
        "awaiting_input": summary["awaiting_input"],
        "paused": summary["paused"],
    }
