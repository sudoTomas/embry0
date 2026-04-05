"""Issues API — create, list, get, update, delete, triage, sync, and activity endpoints."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from legion.api.deps import get_github_sync, get_inputs_repo, get_issue_executor, get_issues_repo, get_jobs_repo
from legion.api.schemas.inputs import AnswerInputRequest, InputResponse
from legion.api.schemas.issues import (
    CreateIssueRequest,
    IssueDetailResponse,
    IssueListResponse,
    IssueResponse,
    UpdateIssueRequest,
)
from legion.audit.helpers import emit_audit
from legion.services.issue_executor import IssueExecutor
from legion.storage.repositories.issue_inputs import IssueInputsRepository
from legion.storage.repositories.issues import IssuesRepository
from legion.storage.repositories.jobs import JobsRepository

logger = structlog.get_logger(__name__)
router = APIRouter()


def _actor(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# POST /issues — create a new issue
# ---------------------------------------------------------------------------


@router.post("/issues", status_code=201)
async def create_issue(
    req: CreateIssueRequest,
    request: Request,
    issues: IssuesRepository = Depends(get_issues_repo),
    sync=Depends(get_github_sync),
    executor: IssueExecutor = Depends(get_issue_executor),
) -> IssueResponse:
    issue_id = await issues.create(
        title=req.title,
        body=req.body,
        priority=req.priority,
        labels=req.labels,
        repo=req.repo,
        github_sync_enabled=req.github_sync_enabled,
        created_by="user",
    )

    config = request.app.state.config
    db = request.app.state.db
    actor = _actor(request)
    await emit_audit(
        db,
        "issue.created",
        actor=actor,
        details={"issue_id": issue_id, "title": req.title},
        audit_log_path=config.audit_log_path,
        issue_id=issue_id,
    )

    if req.auto_triage:
        await issues.update(issue_id, status="triaging")
        await emit_audit(
            db,
            "issue.status_changed",
            actor="system",
            details={"old_status": "open", "new_status": "triaging"},
            audit_log_path=config.audit_log_path,
            issue_id=issue_id,
        )
        try:
            job_id = await executor.execute(issue_id)
            logger.info("auto_triage_started", issue_id=issue_id, job_id=job_id)
        except Exception:
            logger.warning("auto_triage_failed", issue_id=issue_id, exc_info=True)
            await issues.update(issue_id, status="open")

    if req.github_sync_enabled and req.repo:
        if sync is not None:
            try:
                await sync.push_create(issue_id, issues)
            except Exception:
                logger.warning("github_sync_failed", issue_id=issue_id, exc_info=True)

    issue = await issues.get(issue_id)
    return IssueResponse(**issue)


# ---------------------------------------------------------------------------
# GET /issues — list issues with filters
# ---------------------------------------------------------------------------


@router.get("/issues")
async def list_issues(
    status: str | None = None,
    priority: str | None = None,
    repo: str | None = None,
    labels: str | None = None,
    search: str | None = None,
    parent_issue_id: str | None = "__top_level__",
    sort: str = "created_at",
    order: str = "desc",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    issues: IssuesRepository = Depends(get_issues_repo),
) -> IssueListResponse:
    top_level_only = parent_issue_id == "__top_level__"
    rows, total = await issues.list(
        status=status,
        priority=priority,
        repo=repo,
        labels=labels,
        search=search,
        top_level_only=top_level_only,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )
    return IssueListResponse(
        issues=[IssueResponse(**r) for r in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# GET /issues/{issue_id} — get issue detail with children and jobs
# ---------------------------------------------------------------------------


@router.get("/issues/{issue_id}")
async def get_issue(
    issue_id: str,
    issues: IssuesRepository = Depends(get_issues_repo),
    jobs: JobsRepository = Depends(get_jobs_repo),
) -> IssueDetailResponse:
    issue = await issues.get(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    children_rows = await issues.get_children(issue_id)
    children = [IssueResponse(**c) for c in children_rows]

    issue_jobs, _ = await jobs.list(issue_id=issue_id, limit=100, offset=0)

    return IssueDetailResponse(**issue, children=children, jobs=issue_jobs)


# ---------------------------------------------------------------------------
# PUT /issues/{issue_id} — update issue
# ---------------------------------------------------------------------------


@router.put("/issues/{issue_id}")
async def update_issue(
    issue_id: str,
    req: UpdateIssueRequest,
    request: Request,
    issues: IssuesRepository = Depends(get_issues_repo),
    sync=Depends(get_github_sync),
) -> IssueResponse:
    existing = await issues.get(issue_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Issue not found")

    updates = req.model_dump(exclude_none=True)
    if updates:
        await issues.update(issue_id, **updates)

    config = request.app.state.config
    db = request.app.state.db
    actor = _actor(request)

    if "status" in updates and updates["status"] != existing["status"]:
        await emit_audit(
            db,
            "issue.status_changed",
            actor=actor,
            details={
                "issue_id": issue_id,
                "old_status": existing["status"],
                "new_status": updates["status"],
            },
            audit_log_path=config.audit_log_path,
            issue_id=issue_id,
        )
        await issues.update_parent_status(issue_id)

    await emit_audit(
        db,
        "issue.updated",
        actor=actor,
        details={"issue_id": issue_id, "fields": list(updates.keys())},
        audit_log_path=config.audit_log_path,
        issue_id=issue_id,
    )

    if updates.get("github_sync_enabled") or existing.get("github_sync_enabled"):
        if sync is not None:
            try:
                await sync.push_update(issue_id, issues)
            except Exception:
                logger.warning("github_sync_failed", issue_id=issue_id, exc_info=True)

    updated = await issues.get(issue_id)
    return IssueResponse(**updated)


# ---------------------------------------------------------------------------
# DELETE /issues/{issue_id} — soft delete (cancel issue and all children)
# ---------------------------------------------------------------------------


@router.delete("/issues/{issue_id}", status_code=204)
async def delete_issue(
    issue_id: str,
    request: Request,
    issues: IssuesRepository = Depends(get_issues_repo),
    jobs: JobsRepository = Depends(get_jobs_repo),
) -> None:
    existing = await issues.get(issue_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Issue not found")

    children = await issues.get_children(issue_id)
    for child in children:
        await issues.update(child["id"], status="cancelled")

    all_issue_ids = [issue_id] + [c["id"] for c in children]
    for iid in all_issue_ids:
        issue_jobs, _ = await jobs.list(issue_id=iid, limit=100, offset=0)
        for j in issue_jobs:
            if j["status"] in ("pending", "running", "awaiting_input"):
                await jobs.update(j["job_id"], status="cancelled")

    await issues.update(issue_id, status="cancelled")

    config = request.app.state.config
    db = request.app.state.db
    actor = _actor(request)
    await emit_audit(
        db,
        "issue.cancelled",
        actor=actor,
        details={"issue_id": issue_id},
        audit_log_path=config.audit_log_path,
        issue_id=issue_id,
    )


# ---------------------------------------------------------------------------
# POST /issues/{issue_id}/triage — set status to triaging
# ---------------------------------------------------------------------------


@router.post("/issues/{issue_id}/triage")
async def triage_issue(
    issue_id: str,
    request: Request,
    issues: IssuesRepository = Depends(get_issues_repo),
    executor: IssueExecutor = Depends(get_issue_executor),
) -> IssueResponse:
    existing = await issues.get(issue_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Issue not found")

    current_status = existing["status"]
    if current_status in ("triaging", "closed", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot triage issue in '{current_status}' state",
        )

    await issues.update(issue_id, status="triaging")

    config = request.app.state.config
    db = request.app.state.db
    actor = _actor(request)
    await emit_audit(
        db,
        "issue.status_changed",
        actor=actor,
        details={
            "issue_id": issue_id,
            "old_status": current_status,
            "new_status": "triaging",
        },
        audit_log_path=config.audit_log_path,
        issue_id=issue_id,
    )

    try:
        job_id = await executor.execute(issue_id)
        logger.info("triage_started", issue_id=issue_id, job_id=job_id)
    except Exception:
        logger.warning("triage_execution_failed", issue_id=issue_id, exc_info=True)
        await issues.update(issue_id, status="open")

    updated = await issues.get(issue_id)
    return IssueResponse(**updated)


# ---------------------------------------------------------------------------
# POST /issues/{issue_id}/sync — force outbound GitHub sync
# ---------------------------------------------------------------------------


@router.post("/issues/{issue_id}/sync")
async def sync_issue(
    issue_id: str,
    issues: IssuesRepository = Depends(get_issues_repo),
    sync=Depends(get_github_sync),
) -> dict:
    existing = await issues.get(issue_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Issue not found")

    if sync is None:
        raise HTTPException(status_code=400, detail="GitHub sync is not enabled")

    try:
        if existing.get("github_number"):
            await sync.push_update(issue_id, issues)
        else:
            await sync.push_create(issue_id, issues)
    except Exception as exc:
        logger.warning("github_sync_failed", issue_id=issue_id, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"GitHub sync failed: {exc}",
        ) from exc

    return {"issue_id": issue_id, "synced": True}


# ---------------------------------------------------------------------------
# GET /issues/{issue_id}/activity — return audit log entries for issue
# ---------------------------------------------------------------------------


@router.get("/issues/{issue_id}/activity")
async def get_issue_activity(
    issue_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    issues: IssuesRepository = Depends(get_issues_repo),
) -> list[dict]:
    existing = await issues.get(issue_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Issue not found")

    return await issues.get_activity(issue_id, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# Helper: resume pipeline after all blocking inputs are answered
# ---------------------------------------------------------------------------


async def _resume_pipeline(
    issue_id: str,
    issues: IssuesRepository,
    inputs: IssueInputsRepository,
    executor: IssueExecutor,
) -> None:
    # Idempotency guard — bail out if already resumed by another answer path
    issue = await issues.get(issue_id)
    if not issue or issue["status"] != "awaiting_input":
        return

    all_answers = await inputs.list_all_answered(issue_id)
    context_lines = []
    for a in all_answers:
        answer_text = a.get("answer") or a.get("auto_answer") or ""
        context_lines.append(f"Q: {a['question']}\nA: {answer_text}")
    additional_context = "\n\n".join(context_lines)

    # Find the awaiting_input job and resume from its checkpoint
    jobs_repo = executor._jobs
    jobs_list, _ = await jobs_repo.list(issue_id=issue_id, limit=10, offset=0)
    awaiting_jobs = [j for j in jobs_list if j["status"] == "awaiting_input"]

    if awaiting_jobs:
        job_id = awaiting_jobs[0]["job_id"]
        import asyncio

        task = asyncio.create_task(executor.resume(issue_id, job_id, additional_context))
        executor._background_tasks.add(task)
        task.add_done_callback(executor._background_tasks.discard)
        logger.info("pipeline_resumed", issue_id=issue_id, job_id=job_id)
    else:
        # No awaiting job — start fresh with accumulated context
        await issues.update(issue_id, status="triaging")
        try:
            job_id = await executor.execute(issue_id)
            logger.info("pipeline_resumed_fresh", issue_id=issue_id, job_id=job_id)
        except Exception:
            logger.warning("pipeline_resume_failed", issue_id=issue_id, exc_info=True)
            await issues.update(issue_id, status="open")


# ---------------------------------------------------------------------------
# GET /issues/{issue_id}/inputs — list all inputs for an issue
# ---------------------------------------------------------------------------


@router.get("/issues/{issue_id}/inputs")
async def list_issue_inputs(
    issue_id: str,
    issues: IssuesRepository = Depends(get_issues_repo),
    inputs: IssueInputsRepository = Depends(get_inputs_repo),
) -> list[InputResponse]:
    existing = await issues.get(issue_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Issue not found")

    rows = await inputs.list_by_issue(issue_id)
    return [InputResponse(**r) for r in rows]


# ---------------------------------------------------------------------------
# POST /issues/{issue_id}/inputs/{input_id}/answer — submit an answer
# ---------------------------------------------------------------------------


@router.post("/issues/{issue_id}/inputs/{input_id}/answer")
async def answer_issue_input(
    issue_id: str,
    input_id: str,
    req: AnswerInputRequest,
    issues: IssuesRepository = Depends(get_issues_repo),
    inputs: IssueInputsRepository = Depends(get_inputs_repo),
    executor: IssueExecutor = Depends(get_issue_executor),
) -> InputResponse:
    existing = await issues.get(issue_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Issue not found")

    inp = await inputs.get(input_id)
    if not inp:
        raise HTTPException(status_code=404, detail="Input not found")
    if inp["issue_id"] != issue_id:
        raise HTTPException(status_code=404, detail="Input not found")
    if inp["status"] in ("answered", "auto_answered"):
        raise HTTPException(status_code=409, detail="Input has already been answered")

    await inputs.answer(input_id, answer=req.answer, answered_by="user")

    pending_blocking = await inputs.count_pending_blocking(issue_id)
    if pending_blocking == 0:
        await _resume_pipeline(issue_id, issues, inputs, executor)

    updated = await inputs.get(input_id)
    return InputResponse(**updated)
