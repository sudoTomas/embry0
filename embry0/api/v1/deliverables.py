"""Deliverables API — typed job outputs (RAV-603).

- ``GET /jobs/{job_id}/deliverables`` lists what a job produced.
- ``GET /jobs/{job_id}/deliverables/{deliverable_id}/download`` streams the
  artifact's bytes through the orchestrator (same passthrough pattern as the
  per-sub-task qa-artifacts endpoint). Presigned redirects don't work here:
  the orchestrator presigns against the in-compose ``minio:9000`` endpoint,
  which LAN browsers can't resolve — the orchestrator is the only party
  with both MinIO reachability and the artifact's access-control context.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from embry0.api.deps import get_deliverables_repo, get_qa_minio
from embry0.storage.repositories.deliverables import DeliverablesRepository

router = APIRouter()

# Collection caps artifacts at 10MB (plan_route.MAX_ARTIFACT_BYTES); this is a
# defensive ceiling for the in-memory passthrough in case rows ever arrive by
# another path. Mirrors the qa-artifacts passthrough's OOM guard.
_MAX_PASSTHROUGH_BYTES = 50 * 1024 * 1024


@router.get("/jobs/{job_id}/deliverables")
async def list_deliverables(
    job_id: str,
    repo: DeliverablesRepository = Depends(get_deliverables_repo),
) -> list[dict[str, Any]]:
    return await repo.list_for_job(job_id)


@router.get("/jobs/{job_id}/deliverables/{deliverable_id}/download")
async def download_deliverable(
    job_id: str,
    deliverable_id: str,
    repo: DeliverablesRepository = Depends(get_deliverables_repo),
    qa_minio: Any = Depends(get_qa_minio),
) -> Response:
    """Stream an artifact deliverable's bytes with a download disposition."""
    deliverable = await repo.get(deliverable_id)
    if deliverable is None or deliverable.get("job_id") != job_id:
        raise HTTPException(status_code=404, detail="Deliverable not found")
    if deliverable.get("type") != "artifact":
        raise HTTPException(status_code=400, detail="Only artifact deliverables are downloadable")
    bucket = deliverable.get("storage_bucket")
    key = deliverable.get("storage_key")
    if not bucket or not key:
        raise HTTPException(status_code=404, detail="Artifact has no stored object")

    # S3 HEAD before pulling bytes — a huge object must not OOM the
    # orchestrator on first click.
    stat = await qa_minio.stat_object(bucket, key)
    size = stat.get("size")
    if isinstance(size, int) and size > _MAX_PASSTHROUGH_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"artifact too large ({size} bytes > {_MAX_PASSTHROUGH_BYTES} cap)",
        )

    body = await qa_minio.get_object_bytes(bucket, key)
    # basename only, quotes stripped — the title is agent-controlled text and
    # must not be able to smuggle header syntax into Content-Disposition.
    filename = (deliverable.get("title") or key).rsplit("/", 1)[-1].replace('"', "")
    return Response(
        content=body,
        media_type=deliverable.get("media_type") or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, max-age=300",
        },
    )
