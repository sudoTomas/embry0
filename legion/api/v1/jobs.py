"""Jobs API — create, list, get, cancel jobs."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from legion.api.deps import get_jobs_repo
from legion.api.schemas import JobCreateRequest, JobListResponse, JobResponse
from legion.audit.logger import emit_audit_event
from legion.storage.repositories.jobs import JobsRepository

router = APIRouter()


@router.post("/jobs", status_code=201)
async def create_job(req: JobCreateRequest, request: Request, jobs: JobsRepository = Depends(get_jobs_repo)) -> dict:
    job_id = await jobs.create(
        repo=req.repo,
        task=req.task,
        issue_number=req.issue_number,
        pipeline_template=req.pipeline_template,
        pipeline_config=req.pipeline_config,
        sandbox_profile=req.sandbox_profile,
    )
    config = request.app.state.config
    emit_audit_event(
        "job_created",
        actor=request.client.host if request.client else "unknown",
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
    rows, total = await jobs.list(status=status, repo=repo, limit=limit, offset=offset)
    return JobListResponse(jobs=[JobResponse(**r) for r in rows], total=total, offset=offset, limit=limit)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, jobs: JobsRepository = Depends(get_jobs_repo)) -> dict:
    job = await jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs/{job_id}/inputs")
async def get_job_inputs(job_id: str, request: Request) -> list:
    """Get all inputs (questions) for a job."""
    inputs_repo = request.app.state.inputs_repo
    return await inputs_repo.list_by_job(job_id)


@router.post("/jobs/{job_id}/inputs/{input_id}/answer")
async def answer_job_input(job_id: str, input_id: str, request: Request) -> dict:
    """Answer an input question for a job."""
    body = await request.json()
    answer = body.get("answer", "")
    inputs_repo = request.app.state.inputs_repo
    await inputs_repo.answer(input_id, answer, answered_by="user")
    return {"status": "answered", "input_id": input_id}


@router.get("/jobs/{job_id}/logs/events")
async def get_job_log_events(job_id: str, jobs: JobsRepository = Depends(get_jobs_repo)) -> dict:
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
async def resume_job(job_id: str, request: Request, jobs: JobsRepository = Depends(get_jobs_repo)) -> dict:
    """Resume a paused job with optional guidance."""
    job = await jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "paused":
        raise HTTPException(status_code=409, detail=f"Job is {job['status']}, not paused")

    body = await request.json()
    choice = body.get("choice", "continue_retrying")
    guidance = body.get("guidance", "")

    executor = request.app.state.issue_executor
    issue_id = job.get("issue_id")
    if not issue_id:
        raise HTTPException(status_code=400, detail="Job has no associated issue")

    import asyncio
    task = asyncio.create_task(
        executor.resume(issue_id, job_id, {"choice": choice, "guidance": guidance})
    )
    executor._background_tasks.add(task)
    task.add_done_callback(executor._background_tasks.discard)

    return {"job_id": job_id, "status": "resuming", "choice": choice}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request, jobs: JobsRepository = Depends(get_jobs_repo)) -> dict:
    job = await jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] not in ("pending", "running", "awaiting_input"):
        raise HTTPException(status_code=409, detail=f"Cannot cancel job in {job['status']} state")
    await jobs.update(job_id, status="cancelled")
    config = request.app.state.config
    emit_audit_event(
        "job_cancelled",
        actor=request.client.host if request.client else "unknown",
        details={"job_id": job_id},
        audit_log_path=config.audit_log_path,
    )
    return {"job_id": job_id, "status": "cancelled"}
