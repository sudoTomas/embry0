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


# NOTE: Route ordering matters. `get_latest_screenshot` MUST be declared before
# `get_artifact` — FastAPI matches routes in declaration order, and the
# `{path:path}` catch-all in `get_artifact` would otherwise swallow
# `/screenshots/latest` and treat it as an arbitrary artifact path.
@router.get("/jobs/{job_id}/artifacts/screenshots/latest")
async def get_latest_screenshot(
    job_id: str,
    qa_minio: Any = Depends(get_qa_minio),
) -> RedirectResponse:
    # Validate input BEFORE any storage work.
    _check_safe_job_id(job_id)

    # Find every .png under any attempt's screenshots/ for this job. The prefix
    # is `{job_id}/` (not scoped to a single attempt) so a job that has rolled
    # over to a new attempt while the dashboard is polling still surfaces the
    # newest screenshot regardless of which attempt produced it.
    #
    # Use `list_objects_with_meta` so we get `last_modified` from the listing
    # itself — this endpoint is polled every ~5s by the dashboard, and the
    # previous "list + N stat_object" approach was N+1 round trips per poll.
    objs = await qa_minio.list_objects_with_meta("qa-artifacts", prefix=f"{job_id}/")
    screenshots = [
        o for o in objs if o["key"].endswith(".png") and "/screenshots/" in o["key"]
    ]
    if not screenshots:
        raise HTTPException(status_code=404, detail="no screenshots yet")

    # We sort by MinIO's `last_modified` rather than trusting the filename
    # timestamp because:
    #   1) Clock drift inside the sandbox vs. MinIO can make the embedded
    #      timestamp disagree with the actual upload time.
    #   2) An agent could (in principle) upload screenshots out of order or
    #      with a non-conforming filename; we want the freshest *upload*, not
    #      the freshest *filename*.
    #
    # Tie-break: `(last_modified, attempt_int, key)` — equal-mtime screenshots
    # (possible during high-throughput bursts) resolve deterministically by
    # higher attempt number first, then by key as a final tiebreaker. Keys
    # whose `<attempt>` segment is missing or non-numeric are skipped
    # defensively rather than crashing the endpoint.
    def _sort_key(item: dict) -> tuple:
        key = item["key"]
        parts = key.split("/")
        try:
            attempt_int = int(parts[1])
        except (IndexError, ValueError):
            return None
        return (item["last_modified"], attempt_int, key)

    keyed = [(s, _sort_key(s)) for s in screenshots]
    keyed = [(s, k) for s, k in keyed if k is not None]
    if not keyed:
        raise HTTPException(status_code=404, detail="no screenshots yet")
    keyed.sort(key=lambda pair: pair[1], reverse=True)
    latest_key = keyed[0][0]["key"]
    url = await qa_minio.presign_get("qa-artifacts", latest_key, expires_seconds=300)
    return RedirectResponse(url=url, status_code=302)


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
