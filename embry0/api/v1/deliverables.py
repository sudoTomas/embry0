"""Deliverables API — typed job outputs (RAV-603).

- ``GET /jobs/{job_id}/deliverables`` lists what a job produced.
- ``GET /jobs/{job_id}/deliverables/{deliverable_id}/download`` 302-redirects
  to a short-lived MinIO presigned URL for ``artifact`` deliverables (same
  pattern as the qa-artifacts endpoints — the orchestrator holds the MinIO
  creds; browsers only ever see the presigned URL).
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from embry0.api.deps import get_deliverables_repo, get_qa_minio
from embry0.storage.repositories.deliverables import DeliverablesRepository

router = APIRouter()


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
    redirect: bool = True,
    repo: DeliverablesRepository = Depends(get_deliverables_repo),
    qa_minio: Any = Depends(get_qa_minio),
) -> Any:
    """Resolve an artifact deliverable to a presigned MinIO URL.

    Default is a 302 (curl-friendly). ``?redirect=false`` returns
    ``{"url": ...}`` instead — the dashboard needs that because a plain
    browser navigation to this endpoint can't carry the Bearer header, so
    it fetches the URL via the authenticated API client and opens it.
    """
    deliverable = await repo.get(deliverable_id)
    if deliverable is None or deliverable.get("job_id") != job_id:
        raise HTTPException(status_code=404, detail="Deliverable not found")
    if deliverable.get("type") != "artifact":
        raise HTTPException(status_code=400, detail="Only artifact deliverables are downloadable")
    bucket = deliverable.get("storage_bucket")
    key = deliverable.get("storage_key")
    if not bucket or not key:
        raise HTTPException(status_code=404, detail="Artifact has no stored object")
    url = await qa_minio.presign_get(bucket, key, expires_seconds=300)
    if not redirect:
        return {"url": url}
    return RedirectResponse(url=url, status_code=302)
