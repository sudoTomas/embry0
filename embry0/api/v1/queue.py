"""Queue API — real per-status counts for active jobs."""

from fastapi import APIRouter, Depends, Request

from embry0.api.deps import get_jobs_repo
from embry0.api.repo_allowlist import covered_slugs
from embry0.storage.repositories.jobs import JobsRepository

router = APIRouter()


@router.get("/queue")
async def get_queue(request: Request, jobs: JobsRepository = Depends(get_jobs_repo)) -> dict[str, int]:
    """Active-job counts grouped by status.

    Response includes the four non-terminal statuses; `depth` is an alias
    for `pending` retained for backward compatibility with the dashboard.
    A key with a repository allowlist counts only the jobs of the
    repositories it covers.
    """
    summary = await jobs.queue_summary(repos=covered_slugs(request))
    return {
        "depth": summary["pending"],  # back-compat
        "pending": summary["pending"],
        "running": summary["running"],
        "awaiting_input": summary["awaiting_input"],
        "paused": summary["paused"],
    }
