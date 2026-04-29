"""Jobs API — create, list, get, cancel jobs."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from athanor.api.deps import get_jobs_repo
from athanor.api.schemas import JobCreateRequest, JobListResponse, JobResponse
from athanor.api.schemas.jobs import JobResumeRequest
from athanor.audit.helpers import emit_audit
from athanor.storage.repositories.jobs import JobsRepository

router = APIRouter()


@router.post("/jobs", status_code=201)
async def create_job(req: JobCreateRequest, request: Request, jobs: JobsRepository = Depends(get_jobs_repo)) -> dict[str, Any]:
    # Merge any per-agent model override into pipeline_config so triage/dispatch
    # can honour it downstream (see IssueExecutor._run_workflow + agent node).
    effective_config = dict(req.pipeline_config) if req.pipeline_config else None
    if req.agent_models:
        effective_config = {**(effective_config or {}), "agent_models_override": req.agent_models}

    job_id = await jobs.create(
        repo=req.repo,
        task=req.task,
        issue_number=req.issue_number,
        pipeline_template=req.pipeline_template,
        pipeline_config=effective_config,
        sandbox_profile=req.sandbox_profile,
    )
    config = request.app.state.config
    db = getattr(request.app.state, "db", None)
    await emit_audit(
        db,
        "job_created",
        actor=request.client.host if request.client else "api",
        details={"job_id": job_id, "repo": req.repo},
        audit_log_path=config.audit_log_path,
    )
    job = await jobs.get(job_id)
    return job or {"job_id": job_id}


@router.get("/jobs")
async def list_jobs(
    status: str | None = None,
    repo: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    jobs: JobsRepository = Depends(get_jobs_repo),
) -> JobListResponse:
    rows, total = await jobs.list_all(status=status, repo=repo, limit=limit, offset=offset)
    return JobListResponse(jobs=[JobResponse(**r) for r in rows], total=total, offset=offset, limit=limit)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request, jobs: JobsRepository = Depends(get_jobs_repo)) -> dict[str, Any]:
    job = await jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    # Per-agent cost breakdown (E7 lean variant — aggregates existing traces rows).
    traces_repo = getattr(request.app.state, "traces_repo", None)
    if traces_repo is not None:
        try:
            job["cost_breakdown"] = await traces_repo.sum_by_agent_for_job(job_id)
        except Exception:
            import structlog

            structlog.get_logger(__name__).warning("cost_breakdown_query_failed", job_id=job_id, exc_info=True)
            job["cost_breakdown"] = []
    return job


@router.get("/jobs/{job_id}/inputs")
async def get_job_inputs(job_id: str, request: Request) -> list[Any]:
    """Get all inputs (questions) for a job."""
    inputs_repo = request.app.state.inputs_repo
    result: list[Any] = await inputs_repo.list_by_job(job_id)
    return result


@router.get("/jobs/{job_id}/logs/events")
async def get_job_log_events(job_id: str, jobs: JobsRepository = Depends(get_jobs_repo)) -> dict[str, Any]:
    """Get persisted pipeline events for a job."""
    events = await jobs.get_log_events(job_id)
    # Extract the parsed payload from each row
    result = []
    for e in events:
        payload = e.get("payload", {})
        if isinstance(payload, dict):
            result.append(payload)
    return {"events": result}


@router.post("/jobs/{job_id}/resume")
async def resume_job(
    job_id: str,
    req: JobResumeRequest,
    request: Request,
    jobs: JobsRepository = Depends(get_jobs_repo),
) -> dict[str, Any]:
    """Resume a paused job with optional guidance."""
    job = await jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "paused":
        raise HTTPException(status_code=409, detail=f"Job is {job['status']}, not paused")

    executor = request.app.state.issue_executor
    issue_id = job.get("issue_id")
    if not issue_id:
        raise HTTPException(status_code=400, detail="Job has no associated issue")

    executor._track_task(
        executor.resume(issue_id, job_id, {"choice": req.choice, "guidance": req.guidance}),
        kind="resume_via_api",
        job_id=job_id,
        issue_id=issue_id,
    )

    return {"job_id": job_id, "status": "resuming", "choice": req.choice}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request, jobs: JobsRepository = Depends(get_jobs_repo)) -> dict[str, Any]:
    job = await jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] not in ("pending", "running", "awaiting_input", "paused"):
        raise HTTPException(status_code=409, detail=f"Cannot cancel job in {job['status']} state")

    executor = request.app.state.issue_executor
    actor = request.client.host if request.client else "unknown"
    await executor.cancel_job(job_id, actor=actor)

    return {"job_id": job_id, "status": "cancelled"}
