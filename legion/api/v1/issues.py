"""Issues API — create, list, get, update, delete, triage, sync, and activity endpoints."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request

from legion.api.deps import get_issues_repo, get_jobs_repo
from legion.api.schemas.issues import (
    CreateIssueRequest,
    IssueDetailResponse,
    IssueListResponse,
    IssueResponse,
    UpdateIssueRequest,
)
from legion.audit.logger import emit_audit_event
from legion.storage.repositories.issues import IssuesRepository
from legion.storage.repositories.jobs import JobsRepository

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
) -> IssueResponse:
    issue_id = await issues.create(
        title=req.title,
        body=req.body,
        priority=req.priority,
        labels=req.labels,
        repo=req.repo,
        github_sync_enabled=req.github_sync_enabled,
        created_by=_actor(request),
    )

    if req.auto_triage:
        await issues.update(issue_id, status="triaging")

    config = request.app.state.config
    emit_audit_event(
        "issue.created",
        actor=_actor(request),
        details={"issue_id": issue_id, "title": req.title},
        audit_log_path=config.audit_log_path,
        issue_id=issue_id,
    )

    if req.github_sync_enabled and req.repo:
        sync = request.app.state.github_sync
        if sync is not None:
            try:
                await sync.push_create(issue_id)
            except Exception:
                pass

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
    limit: int = 50,
    offset: int = 0,
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

    jobs_rows, _ = await jobs.list(limit=200, offset=0)
    issue_jobs = [j for j in jobs_rows if j.get("issue_id") == issue_id]

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
) -> IssueResponse:
    existing = await issues.get(issue_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Issue not found")

    updates = req.model_dump(exclude_none=True)
    if updates:
        if "labels" in updates:
            updates["labels"] = json.dumps(updates["labels"])
        await issues.update(issue_id, **updates)

    config = request.app.state.config
    actor = _actor(request)

    if "status" in updates and updates["status"] != existing["status"]:
        emit_audit_event(
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

    emit_audit_event(
        "issue.updated",
        actor=actor,
        details={"issue_id": issue_id, "fields": list(updates.keys())},
        audit_log_path=config.audit_log_path,
        issue_id=issue_id,
    )

    if updates.get("github_sync_enabled") or existing.get("github_sync_enabled"):
        sync = request.app.state.github_sync
        if sync is not None:
            try:
                await sync.push_update(issue_id)
            except Exception:
                pass

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
) -> None:
    existing = await issues.get(issue_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Issue not found")

    children = await issues.get_children(issue_id)
    for child in children:
        await issues.update(child["id"], status="cancelled")

    await issues.update(issue_id, status="cancelled")

    config = request.app.state.config
    emit_audit_event(
        "issue.cancelled",
        actor=_actor(request),
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
    emit_audit_event(
        "issue.status_changed",
        actor=_actor(request),
        details={
            "issue_id": issue_id,
            "old_status": current_status,
            "new_status": "triaging",
        },
        audit_log_path=config.audit_log_path,
        issue_id=issue_id,
    )

    updated = await issues.get(issue_id)
    return IssueResponse(**updated)


# ---------------------------------------------------------------------------
# POST /issues/{issue_id}/sync — force outbound GitHub sync
# ---------------------------------------------------------------------------

@router.post("/issues/{issue_id}/sync")
async def sync_issue(
    issue_id: str,
    request: Request,
    issues: IssuesRepository = Depends(get_issues_repo),
) -> dict:
    existing = await issues.get(issue_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Issue not found")

    sync = request.app.state.github_sync
    if sync is None:
        raise HTTPException(status_code=400, detail="GitHub sync is not enabled")

    if existing.get("github_number"):
        await sync.push_update(issue_id)
    else:
        await sync.push_create(issue_id)

    return {"issue_id": issue_id, "synced": True}


# ---------------------------------------------------------------------------
# GET /issues/{issue_id}/activity — return audit log entries for issue
# ---------------------------------------------------------------------------

@router.get("/issues/{issue_id}/activity")
async def get_issue_activity(
    issue_id: str,
    limit: int = 50,
    offset: int = 0,
    issues: IssuesRepository = Depends(get_issues_repo),
) -> list[dict]:
    existing = await issues.get(issue_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Issue not found")

    return await issues.get_activity(issue_id, limit=limit, offset=offset)
