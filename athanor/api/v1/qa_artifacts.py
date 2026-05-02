"""QA artifact serving — presigned GET redirects + SSE log streams.

Path layout (bucket: qa-artifacts):
    <job_id>/<attempt_n>/result.json
    <job_id>/<attempt_n>/logs/full.log
    <job_id>/<attempt_n>/logs/<service>.log
    <job_id>/<attempt_n>/screenshots/<phase>/<ts>-<slug>.png
    <job_id>/<attempt_n>/traces/<criterion-slug>.zip

The "latest attempt" is the highest <attempt_n> directory present in MinIO
under <job_id>/. Routes that don't specify an attempt resolve it dynamically.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

router = APIRouter()

_SAFE_ARTIFACT_PATH = re.compile(r"^[A-Za-z0-9._:\-]+(/[A-Za-z0-9._:\-]+)*$")


def _check_safe(path: str) -> None:
    if ".." in path.split("/") or not _SAFE_ARTIFACT_PATH.match(path):
        raise HTTPException(status_code=404, detail="not found")


async def _resolve_latest_attempt(minio, job_id: str) -> int | None:
    """Highest <attempt_n> with any object in MinIO under <job_id>/."""
    objs = await minio.list_objects("qa-artifacts", prefix=f"{job_id}/")
    attempts: set[int] = set()
    for o in objs:
        # o = "JOB/2/result.json"  -> "2"
        parts = o.split("/")
        if len(parts) >= 2 and parts[1].isdigit():
            attempts.add(int(parts[1]))
    return max(attempts) if attempts else None


@router.get("/jobs/{job_id}/artifacts/{path:path}")
async def get_artifact(job_id: str, path: str, request: Request) -> RedirectResponse:
    minio = getattr(request.app.state, "qa_minio", None)
    if minio is None:
        raise HTTPException(status_code=503, detail="QA artifact storage unavailable")
    _check_safe(path)

    # Resolve latest attempt for this job.
    n = await _resolve_latest_attempt(minio, job_id)
    if n is None:
        raise HTTPException(status_code=404, detail="no attempts found")
    key = f"{job_id}/{n}/{path}"
    url = await minio.presign_get("qa-artifacts", key, expires_seconds=300)
    return RedirectResponse(url=url, status_code=302)
