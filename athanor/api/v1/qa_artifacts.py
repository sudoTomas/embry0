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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from athanor.api.deps import get_qa_minio

router = APIRouter()

_SAFE_ARTIFACT_PATH = re.compile(r"^[A-Za-z0-9._:\-]+(/[A-Za-z0-9._:\-]+)*$")
_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9_\-]+$")


def _check_safe(path: str) -> None:
    # Reject empty segments and segments that are entirely dots (`.`, `..`,
    # `...`, `....`, ...) before the regex check — the regex's character class
    # allows `.`, so a segment like `...` would otherwise sneak through.
    for seg in path.split("/"):
        if not seg or set(seg) == {"."}:
            raise HTTPException(status_code=404, detail="not found")
    if not _SAFE_ARTIFACT_PATH.match(path):
        raise HTTPException(status_code=404, detail="not found")


def _check_safe_job_id(job_id: str) -> None:
    # Defense-in-depth: job_id is concatenated into the MinIO prefix, so reject
    # anything that isn't a plain identifier even though FastAPI's path-param
    # decoding already strips slashes.
    if not job_id or not _SAFE_JOB_ID.match(job_id):
        raise HTTPException(status_code=404, detail="not found")


async def _resolve_latest_attempt(minio: Any, job_id: str) -> int | None:
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
async def get_artifact(
    job_id: str,
    path: str,
    qa_minio: Any = Depends(get_qa_minio),
) -> RedirectResponse:
    # Validate inputs BEFORE any storage work.
    _check_safe_job_id(job_id)
    _check_safe(path)

    # Resolve latest attempt for this job.
    n = await _resolve_latest_attempt(qa_minio, job_id)
    if n is None:
        raise HTTPException(status_code=404, detail="no attempts found")
    key = f"{job_id}/{n}/{path}"
    url = await qa_minio.presign_get("qa-artifacts", key, expires_seconds=300)
    return RedirectResponse(url=url, status_code=302)
