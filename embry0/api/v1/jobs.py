"""Jobs API — create, list, get, cancel jobs."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from embry0.api.deps import get_jobs_repo
from embry0.api.schemas import JobCreateRequest, JobListResponse, JobResponse, QAJobOverrides
from embry0.api.schemas.jobs import JobResumeRequest
from embry0.audit.helpers import emit_audit
from embry0.storage.repositories.jobs import JobsRepository

router = APIRouter()


class AnswerJobInputRequest(BaseModel):
    answer: str


@router.post("/jobs", status_code=201)
async def create_job(
    req: JobCreateRequest, request: Request, jobs: JobsRepository = Depends(get_jobs_repo)
) -> dict[str, Any]:
    # QA pipeline takes a different code path: no triage, no issue, dispatched
    # via IssueExecutor.start_job which seeds the QA initial state.
    if req.pipeline == "qa":
        if not req.branch:
            raise HTTPException(status_code=400, detail="qa pipeline requires 'branch'")
        executor = getattr(request.app.state, "issue_executor", None)
        if executor is None:
            raise HTTPException(status_code=503, detail="executor not initialized")
        qa_overrides = (req.qa or QAJobOverrides()).model_dump()
        try:
            job_id = await executor.start_job(
                repo=req.repo,
                branch=req.branch,
                pipeline="qa",
                qa_overrides=qa_overrides,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        config = request.app.state.config
        await emit_audit(
            getattr(request.app.state, "db", None),
            "qa_job_created",
            actor=request.client.host if request.client else "api",
            details={"job_id": job_id, "repo": req.repo, "branch": req.branch},
            audit_log_path=config.audit_log_path,
        )
        job = await jobs.get(job_id)
        return job or {"job_id": job_id}

    # Legacy issue-to-pr path. ``task`` non-empty is enforced at the schema
    # layer (JobCreateRequest._enforce_task_for_non_qa) — assertion below is a
    # safety net for type checkers.
    assert req.task is not None  # noqa: S101 — schema-enforced

    # Merge any per-agent model override into pipeline_config so triage/dispatch
    # can honour it downstream (see IssueExecutor._run_workflow + agent node).
    effective_config = dict(req.pipeline_config) if req.pipeline_config else None
    if req.agent_models:
        effective_config = {**(effective_config or {}), "agent_models_override": req.agent_models}

    trace_id = f"trc-{uuid.uuid4().hex[:12]}"
    job_id = await jobs.create(
        repo=req.repo,
        task=req.task,
        issue_number=req.issue_number,
        pipeline_template=req.pipeline_template,
        pipeline_config=effective_config,
        sandbox_profile=req.sandbox_profile,
        trace_id=trace_id,
        context=req.context.model_dump() if req.context else None,
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

    # Dispatch the issue-to-pr workflow in the background. Without this the row
    # would sit in `pending` forever: the direct POST /jobs path created the job
    # but never ran it (only the issues path dispatched the workflow). The
    # workflow tolerates a null issue_id for an issue-less, API-submitted job.
    executor = getattr(request.app.state, "issue_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="executor not initialized")
    synthetic_issue = {
        "repo": req.repo,
        "title": req.task,
        "body": "",
        "github_number": req.issue_number,
    }
    executor._track_task(
        executor._run_workflow(None, job_id, synthetic_issue),
        kind="workflow_execute",
        job_id=job_id,
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


@router.post("/jobs/{job_id}/inputs/{input_id}/answer")
async def answer_job_input(
    job_id: str,
    input_id: str,
    req: AnswerJobInputRequest,
    request: Request,
    jobs: JobsRepository = Depends(get_jobs_repo),
) -> dict[str, Any]:
    """Answer an agent question on a job — issue-less-job path (EMB-43).

    Direct POST /jobs runs have no issue row, so the issue-scoped answer
    endpoint cannot serve them. Mirrors its contract: records the answer,
    and when no blocking questions remain pending for the job, resumes the
    workflow from its checkpoint with the accumulated Q&A as context.
    Issue-backed inputs should keep using the issues endpoint (its resume
    path also updates the issue row), but this one tolerates them.
    """
    job = await jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    inputs_repo = request.app.state.inputs_repo
    inp = await inputs_repo.get(input_id)
    if not inp or inp["job_id"] != job_id:
        raise HTTPException(status_code=404, detail="Input not found")
    if inp["status"] in ("answered", "auto_answered"):
        raise HTTPException(status_code=409, detail="Input has already been answered")

    await inputs_repo.answer(input_id, answer=req.answer, answered_by="user")

    pending = await inputs_repo.count_pending_blocking_for_job(job_id)
    if pending == 0:
        fresh = await jobs.get(job_id)
        if fresh and fresh["status"] == "awaiting_input":
            answered = await inputs_repo.list_all_answered_for_job(job_id)
            context_lines = [
                f"Q: {a['question']}\nA: {a.get('answer') or a.get('auto_answer') or ''}" for a in answered
            ]
            executor = request.app.state.issue_executor
            executor._track_task(
                executor.resume(inp.get("issue_id") or None, job_id, "\n\n".join(context_lines)),
                kind="resume_via_job_answer",
                job_id=job_id,
                issue_id=inp.get("issue_id") or None,
            )

    updated = await inputs_repo.get(input_id)
    return dict(updated or {})


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
    # issue_id is None for issue-less, API-submitted jobs (POST /jobs with a
    # task string). The workflow tolerates that on first run (_run_workflow is
    # dispatched with None from create_job) and IssuesRepository.update()
    # no-ops on a missing issue, so resume must tolerate it too — otherwise
    # every paused API job is unrecoverable (its interrupt options can never
    # be exercised; only /cancel works).
    issue_id = job.get("issue_id") or None

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
