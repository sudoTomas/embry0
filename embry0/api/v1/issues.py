"""Issues API — create, list, get, update, delete, triage, sync, and activity endpoints.

To a key with a repository allowlist, an issue of another repository, or of
none, reads and is acted on as an issue that does not exist, and the lists
leave it out (:mod:`embry0.api.issue_allowlist`).
"""

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from embry0.api.deps import (
    get_github_sync,
    get_inputs_repo,
    get_issue_executor,
    get_issues_repo,
    get_jobs_repo,
    request_actor,
)
from embry0.api.issue_allowlist import hide_input_unless_covered, parent_hidden, without_hidden_parents
from embry0.api.repo_allowlist import covered_slugs, enforce_reach, enforce_repo_allowlist, hide_unless_covered
from embry0.api.schemas import RunWithRequest
from embry0.api.schemas.inputs import AnswerInputRequest, InputResponse
from embry0.api.schemas.issues import (
    CreateIssueRequest,
    IssueDetailResponse,
    IssueDispatchResponse,
    IssueListResponse,
    IssueResponse,
    UpdateIssueRequest,
)
from embry0.api.served import ServedRoute
from embry0.api.v1.run_with import checked_run_with
from embry0.audit.helpers import emit_audit
from embry0.safety.error_codes import ErrorCode
from embry0.services.answer_recorder import answers_context, record_answer
from embry0.services.issue_executor import IssueExecutor
from embry0.services.run_with import RunWith
from embry0.storage.repositories.issue_inputs import IssueInputsRepository
from embry0.storage.repositories.issues import IssuesRepository
from embry0.storage.repositories.jobs import JobsRepository, job_repository
from embry0.storage.repositories.repos import UnregisteredRepoError
from embry0.trackers import github_number_of

logger = structlog.get_logger(__name__)
router = APIRouter(route_class=ServedRoute)


async def _dispatch(
    executor: IssueExecutor,
    issues: IssuesRepository,
    issue_id: str,
    request: Request,
    *,
    failure_event: str,
    run_with: RunWith | None = None,
) -> dict[str, str | None]:
    """Start a job for an issue already moved to ``triaging``; report, never raise.

    Returns the ``job_id`` / ``dispatch_error_code`` / ``dispatch_error`` fields
    of :class:`IssueDispatchResponse`. On failure the issue goes back to
    ``open`` so the caller can retry, and the client gets a stable code and a
    safe sentence: exception text can carry tokens, paths and internal
    hostnames, so it is logged here and nowhere else (#284).
    """
    try:
        chosen: dict[str, Any] = {"run_with": run_with} if run_with is not None else {}
        job_id = await executor.execute(issue_id, dispatched_by=request_actor(request), **chosen)
    except UnregisteredRepoError as exc:
        # #360: the refusal names only the repo and what to do, so it is safe
        # to hand the client as it is.
        logger.warning(failure_event, issue_id=issue_id, error_code=ErrorCode.REPO_UNREGISTERED.value)
        await issues.update(issue_id, status="open")
        return {"job_id": None, "dispatch_error_code": ErrorCode.REPO_UNREGISTERED.value, "dispatch_error": str(exc)}
    except Exception:
        logger.warning(failure_event, issue_id=issue_id, exc_info=True)
        await issues.update(issue_id, status="open")
        return {
            "job_id": None,
            "dispatch_error_code": ErrorCode.DISPATCH_FAILED.value,
            "dispatch_error": (
                f"The issue was saved but no job was started; it is back in `open`. "
                f"Retry with POST /issues/{issue_id}/triage. The cause is in the orchestrator log "
                f"(event `{failure_event}`, issue_id={issue_id})."
            ),
        }
    return {"job_id": job_id, "dispatch_error_code": None, "dispatch_error": None}


def _actor(request: Request) -> str:
    return request_actor(request)


def _issue_not_found() -> HTTPException:
    """What the routes that read or act on an issue answer for an issue that does not exist, and for one the caller's
    key does not cover (``embry0.api.issue_allowlist``)."""
    return HTTPException(status_code=404, detail="Issue not found")


def _input_not_found() -> HTTPException:
    """What answering a question answers for a question that does not exist, and for one the caller's key does not
    cover (``embry0.api.issue_allowlist``)."""
    return HTTPException(status_code=404, detail="Input not found")


#: Why answering an issue's question is refused when it would resume a job the key does not cover.
ANSWER_REACHES_BEYOND = (
    "the answer is the issue's last blocking one, and it would resume the issue's waiting job, which is in a "
    "repository the key does not cover"
)
#: The job statuses deleting an issue cancels.
_ACTIVE_JOB_STATUSES = ("pending", "running", "awaiting_input", "paused")
#: Why deleting an issue the key reads is refused when it reaches another repository.
DELETE_REACHES_BEYOND = (
    "the issue has a child issue or an active job in a repository the key does not cover, which deleting it "
    "would cancel"
)


# ---------------------------------------------------------------------------
# POST /issues — create a new issue
# ---------------------------------------------------------------------------


@router.post("/issues", status_code=201)
async def create_issue(
    req: CreateIssueRequest,
    request: Request,
    issues: IssuesRepository = Depends(get_issues_repo),
    sync: Any = Depends(get_github_sync),
    executor: IssueExecutor = Depends(get_issue_executor),
) -> IssueDispatchResponse:
    await enforce_repo_allowlist(request, req.repo, action="create an issue")
    issue_id = await issues.create(
        title=req.title,
        body=req.body,
        priority=req.priority,
        labels=req.labels,
        repo=req.repo,
        github_sync_enabled=req.github_sync_enabled,
        created_by="user",
        # Convert AskUserChannel enum members to plain strings for the jsonb
        # column. Passing the raw StrEnum members through asyncpg's jsonb
        # encoder works (StrEnum is a str subclass) but storing strings keeps
        # the column free of any enum-class coupling.
        notification_channels=[c.value for c in req.notification_channels],
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

    dispatch: dict[str, str | None] = {}
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
        dispatch = await _dispatch(executor, issues, issue_id, request, failure_event="auto_triage_failed")
        if dispatch["job_id"]:
            logger.info("auto_triage_started", issue_id=issue_id, job_id=dispatch["job_id"])

    if req.github_sync_enabled and req.repo:
        if sync is not None:
            try:
                await sync.push_create(issue_id, issues)
            except Exception:
                logger.warning("github_sync_failed", issue_id=issue_id, exc_info=True)

    issue = await issues.get(issue_id)
    assert issue is not None, "Newly created issue must exist"
    return IssueDispatchResponse(**{**issue, **dispatch})


# ---------------------------------------------------------------------------
# GET /issues — list issues with filters
# ---------------------------------------------------------------------------


@router.get("/issues")
async def list_issues(
    request: Request,
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
    rows, total = await issues.list_all(
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
        repos=covered_slugs(request),
    )
    # An issue names no parent the key does not read.
    await without_hidden_parents(request, rows)
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
    request: Request,
    issues: IssuesRepository = Depends(get_issues_repo),
    jobs: JobsRepository = Depends(get_jobs_repo),
) -> IssueDetailResponse:
    covered = covered_slugs(request)
    issue = await issues.get(issue_id, repos=covered)
    if not issue:
        raise _issue_not_found()
    await without_hidden_parents(request, [issue])

    children_rows = await issues.get_children(issue_id, repos=covered)
    children = [IssueResponse(**c) for c in children_rows]

    issue_jobs, _ = await jobs.list_all(issue_id=issue_id, limit=100, offset=0, repos=covered)

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
    sync: Any = Depends(get_github_sync),
) -> IssueResponse:
    existing = await issues.get(issue_id)
    if not existing:
        raise _issue_not_found()

    updates = req.model_dump(exclude_none=True)
    # Both ends: a restricted key may neither edit another repository's
    # issue, which answers as one that does not exist, nor move one of its
    # own issues to a repository it may not use.
    await hide_unless_covered(request, existing.get("repo"), _issue_not_found(), action="update an issue")
    if "repo" in updates:
        await enforce_repo_allowlist(request, updates["repo"], action="move an issue")
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
        # A key's change rolls up into no parent it does not read; skipped rather than refused, which would tell
        # the parent exists (embry0.api.issue_allowlist).
        if not await parent_hidden(request, existing):
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

    updated = await issues.get(issue_id, repos=covered_slugs(request))
    assert updated is not None, "Updated issue must exist"
    await without_hidden_parents(request, [updated])
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
        raise _issue_not_found()
    await hide_unless_covered(request, existing.get("repo"), _issue_not_found(), action="delete an issue")

    children = await issues.get_children(issue_id)
    all_issue_ids = [issue_id] + [c["id"] for c in children]
    active = [
        j
        for iid in all_issue_ids
        for j in (await jobs.list_all(issue_id=iid, limit=100, offset=0))[0]
        if j["status"] in _ACTIVE_JOB_STATUSES
    ]
    # Everything this cancels, the children and their active jobs included, is checked before any write. The issue
    # is the key's; a child or a job elsewhere is refused without naming where.
    await enforce_reach(
        request,
        str(existing.get("repo")),
        [*(c.get("repo") for c in children), *(job_repository(j) for j in active)],
        action="delete an issue",
        reason=DELETE_REACHES_BEYOND,
    )

    # #449: stop every active job through the executor, as POST
    # /jobs/{id}/cancel does — it cancels the task, destroys the sandbox and
    # closes the credential ledger. Writing the row alone left the workflow
    # running. Jobs first: cancel_job reopens the job's issue, so the issues
    # are marked cancelled after it.
    executor = request.app.state.issue_executor
    actor = _actor(request)
    for j in active:
        await executor.cancel_job(j["job_id"], actor=actor)

    for child in children:
        await issues.update(child["id"], status="cancelled")
    await issues.update(issue_id, status="cancelled")

    config = request.app.state.config
    db = request.app.state.db
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


class TriageBody(BaseModel):
    """Optional: the model and key the issue's job runs with (spec 2026-10-25)."""

    model_config = ConfigDict(extra="forbid")

    run_with: RunWithRequest | None = None


@router.post("/issues/{issue_id}/triage")
async def triage_issue(
    issue_id: str,
    request: Request,
    issues: IssuesRepository = Depends(get_issues_repo),
    executor: IssueExecutor = Depends(get_issue_executor),
    body: TriageBody | None = None,
) -> IssueDispatchResponse:
    existing = await issues.get(issue_id)
    if not existing:
        raise _issue_not_found()

    await hide_unless_covered(request, existing.get("repo"), _issue_not_found(), action="triage an issue")
    run_with = await checked_run_with(request, body.run_with if body else None, existing.get("repo"))
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

    # Same contract as POST /issues: a 200 whose dispatch failed says so.
    dispatch = await _dispatch(
        executor, issues, issue_id, request, failure_event="triage_execution_failed", run_with=run_with
    )
    if dispatch["job_id"]:
        logger.info("triage_started", issue_id=issue_id, job_id=dispatch["job_id"])

    updated = await issues.get(issue_id, repos=covered_slugs(request))
    assert updated is not None, "Triaged issue must exist"
    await without_hidden_parents(request, [updated])
    return IssueDispatchResponse(**{**updated, **dispatch})


# ---------------------------------------------------------------------------
# POST /issues/{issue_id}/sync — force outbound GitHub sync
# ---------------------------------------------------------------------------


@router.post("/issues/{issue_id}/sync")
async def sync_issue(
    issue_id: str,
    request: Request,
    issues: IssuesRepository = Depends(get_issues_repo),
    sync: Any = Depends(get_github_sync),
) -> dict[str, object]:
    existing = await issues.get(issue_id)
    if not existing:
        raise _issue_not_found()
    await hide_unless_covered(request, existing.get("repo"), _issue_not_found(), action="sync an issue")

    if sync is None:
        raise HTTPException(status_code=400, detail="GitHub sync is not enabled")

    try:
        if github_number_of(existing) is not None:
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
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    issues: IssuesRepository = Depends(get_issues_repo),
) -> list[dict[str, Any]]:
    existing = await issues.get(issue_id, repos=covered_slugs(request))
    if not existing:
        raise _issue_not_found()

    return await issues.get_activity(issue_id, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# Helper: resume pipeline after all blocking inputs are answered
# ---------------------------------------------------------------------------


async def _resume_pipeline(
    issue_id: str,
    issues: IssuesRepository,
    inputs: IssueInputsRepository,
    executor: IssueExecutor,
    dispatched_by: str | None = None,
) -> None:
    # Idempotency guard — bail out if already resumed by another answer path
    issue = await issues.get(issue_id)
    if not issue or issue["status"] != "awaiting_input":
        return

    additional_context = answers_context(await inputs.list_all_answered(issue_id))
    # resume_or_execute takes the issue first (#499): an answer that finds it taken goes on with nothing.
    await executor.resume_or_execute(issue_id, additional_context, dispatched_by=dispatched_by)


async def _refuse_a_resume_elsewhere(
    request: Request,
    issue: dict[str, Any],
    inp: dict[str, Any],
    inputs: IssueInputsRepository,
    executor: IssueExecutor,
    *,
    action: str,
) -> None:
    """Refuse, before anything is written, an answer of a key with an allowlist that would resume a job the key does
    not cover: the issue's last blocking answer resumes its newest waiting job (``IssueExecutor.waiting_job``), which
    may be another repository's once the issue moved. The refusal names the issue's repository only."""
    if covered_slugs(request) is None or issue.get("status") != "awaiting_input":
        return
    answered = inp.get("importance") == "blocking" and inp.get("status") == "pending"
    if await inputs.count_pending_blocking(str(issue["id"])) - int(answered) > 0:
        return
    waiting = await executor.waiting_job(str(issue["id"]))
    if waiting is not None:
        await enforce_reach(
            request, str(issue.get("repo")), [job_repository(waiting)], action=action, reason=ANSWER_REACHES_BEYOND
        )


# ---------------------------------------------------------------------------
# GET /issues/{issue_id}/inputs — list all inputs for an issue
# ---------------------------------------------------------------------------


@router.get("/issues/{issue_id}/inputs")
async def list_issue_inputs(
    issue_id: str,
    request: Request,
    issues: IssuesRepository = Depends(get_issues_repo),
    inputs: IssueInputsRepository = Depends(get_inputs_repo),
) -> list[InputResponse]:
    covered = covered_slugs(request)
    existing = await issues.get(issue_id, repos=covered)
    if not existing:
        raise _issue_not_found()

    rows = await inputs.list_by_issue(issue_id, repos=covered)
    return [InputResponse(**r) for r in rows]


# ---------------------------------------------------------------------------
# POST /issues/{issue_id}/inputs/{input_id}/answer — submit an answer
# ---------------------------------------------------------------------------


@router.post("/issues/{issue_id}/inputs/{input_id}/answer")
async def answer_issue_input(
    issue_id: str,
    input_id: str,
    req: AnswerInputRequest,
    request: Request,
    issues: IssuesRepository = Depends(get_issues_repo),
    inputs: IssueInputsRepository = Depends(get_inputs_repo),
    executor: IssueExecutor = Depends(get_issue_executor),
) -> InputResponse:
    return await answer_issue_input_as(issue_id, input_id, req, request, issues, inputs, executor, _actor(request))


async def answer_issue_input_as(
    issue_id: str,
    input_id: str,
    req: AnswerInputRequest,
    request: Request,
    issues: IssuesRepository,
    inputs: IssueInputsRepository,
    executor: IssueExecutor,
    actor: str,
) -> InputResponse:
    """The answer as ``actor``: whoever authenticated, or the person a chat assistant verified."""
    existing = await issues.get(issue_id)
    if not existing:
        raise _issue_not_found()
    # An answer resumes the issue's job once no blocking question is left.
    action = "answer a question on an issue"
    await hide_unless_covered(request, existing.get("repo"), _issue_not_found(), action=action)

    inp = await inputs.get(input_id)
    if not inp:
        raise _input_not_found()
    if inp["issue_id"] != issue_id:
        raise _input_not_found()
    # A question asked by a job of another repository answers as one that does not exist.
    await hide_input_unless_covered(request, inp, _input_not_found(), action=action)
    await _refuse_a_resume_elsewhere(request, existing, inp, inputs, executor, action=action)
    # RAV-1059: auto_answered rows stay answerable — a human answer overrides
    # the orchestrator's auto-answer post-hoc (the state.py contract; the
    # job-scoped endpoint carries the same rule).
    if inp["status"] == "answered":
        raise HTTPException(status_code=409, detail="Input has already been answered")

    if executor.draining:
        # #499: an answer recorded now could not resume the job, and could not be sent again once recorded.
        raise HTTPException(status_code=503, detail="embry0 is shutting down; answer again once it is back")
    # RAV-1076: shared record path (persist + Telegram mirror + Linear Q&A
    # comment); answered_by carries the real principal.
    recorded = await record_answer(
        inp=inp,
        answer=req.answer,
        answered_by=actor,
        channel="api",
        inputs_repo=inputs,
        issues_repo=issues,
        tracker_channels=getattr(request.app.state, "tracker_channels", None),
        config=getattr(request.app.state, "config", None),
    )
    if recorded is False:
        # #499: another answer to this question was taken first, between the read above and this write.
        raise HTTPException(status_code=409, detail="Input has already been answered")

    pending_blocking = await inputs.count_pending_blocking(issue_id)
    if pending_blocking == 0:
        await _resume_pipeline(issue_id, issues, inputs, executor, dispatched_by=actor)

    updated = await inputs.get(input_id)
    assert updated is not None, "Answered input must exist"
    return InputResponse(**updated)
