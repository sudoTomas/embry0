"""Traces API."""

from fastapi import APIRouter, Depends

from athanor.api.deps import get_traces_repo
from athanor.storage.repositories.traces import TracesRepository

router = APIRouter()


@router.get("/traces")
async def list_traces(
    job_id: str | None = None,
    agent_type: str | None = None,
    result: str | None = None,
    limit: int = 50,
    offset: int = 0,
    traces: TracesRepository = Depends(get_traces_repo),
) -> dict[str, object]:
    rows, total = await traces.list(job_id=job_id, agent_type=agent_type, result=result, limit=limit, offset=offset)
    return {"traces": rows, "total": total, "offset": offset, "limit": limit}
