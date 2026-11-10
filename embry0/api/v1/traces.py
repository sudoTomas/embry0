"""Traces API: the agents' runs.

A run belongs to its job, and a job to the repository it works on. A key
with a repository allowlist lists only the runs of the jobs it reads
(``embry0.api.job_allowlist``): the list and its ``total`` leave out every
other run in their query, before the page, so ``?job_id=`` another
repository's job answers as a job with no runs. A page holds 1 to 200 runs
(``limit``) from ``offset`` 0 on; anything else is a 422.
"""

from fastapi import APIRouter, Depends, Query, Request

from embry0.api.deps import get_traces_repo
from embry0.api.repo_allowlist import covered_slugs
from embry0.storage.repositories.traces import TracesRepository

router = APIRouter()


@router.get("/traces")
async def list_traces(
    request: Request,
    job_id: str | None = None,
    agent_type: str | None = None,
    result: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    traces: TracesRepository = Depends(get_traces_repo),
) -> dict[str, object]:
    rows, total = await traces.list_all(
        job_id=job_id, agent_type=agent_type, result=result, limit=limit, offset=offset, repos=covered_slugs(request)
    )
    return {"traces": rows, "total": total, "offset": offset, "limit": limit}
