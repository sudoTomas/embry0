"""What the retention sweeps would remove now (ADR 0017): an admin dry run.

``GET /admin/retention/preview`` runs the three sweeps with ``dry_run=True``, the
same code that removes, so the counts are what a sweep at this moment would
do. Each sweep runs at its configured number of days, or at the one given in
the query, so an operator can see the effect of a value before setting it.
A sweep with neither reports zeros and ``retention_days: null``.

Admin tier: the answer names the QA images and volumes it would remove, and
the dry run reads every old job event.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from embry0.api.deps import require_admin

router = APIRouter()


@router.get("/admin/retention/preview", dependencies=[require_admin])
async def retention_preview(
    request: Request,
    job_log_days: int | None = Query(default=None, gt=0, le=3650),
    agent_session_days: int | None = Query(default=None, gt=0, le=3650),
    qa_image_days: int | None = Query(default=None, gt=0, le=3650),
) -> dict[str, Any]:
    state = request.app.state
    job_logs = getattr(state, "job_log_retention", None)
    sessions = getattr(state, "agent_session_retention", None)
    qa_images = getattr(state, "qa_image_retention", None)
    if job_logs is None or sessions is None or qa_images is None:
        raise HTTPException(status_code=503, detail="retention sweeps not initialized")
    logs = await job_logs.run_once(dry_run=True, retention_days=job_log_days)
    expired = await sessions.run_once(dry_run=True, retention_days=agent_session_days)
    images = await qa_images.run_once(dry_run=True, retention_days=qa_image_days)
    return {
        "job_logs": {"configured_days": job_logs.retention_days, **logs.as_dict()},
        "agent_sessions": {"configured_days": sessions.retention_days, **expired.as_dict()},
        "qa_images": {"configured_days": qa_images.retention_days, **images.as_dict()},
    }
