"""Healing-case API (#278) — what happened to each finding, and accepting one.

Reads, plus the two decisions only a human can make. Everything else on a
case is written by the pipeline itself through ``HealingCaseService``, never
by API callers.

``POST /cases/{id}/accept`` (#378, #460): a watcher finding is accepted here
instead of in its tracker, and the acceptance is the same dispatch the label
webhook would have run — the case is linked and accepted by the job. How the
draft is dispatched is its tracker's business (``_ACCEPTORS``): a GitHub
draft is read back and dispatched; a Linear draft gets the trigger label and
goes through the Linear trigger path, label webhook and all. A case a
``not_healed`` verdict sent back (#658) is accepted here too, and its retry is
dispatched on the issue the failed attempt ran on (``_accept_retry``).

``POST /cases/{id}/close`` (#410): the other end. A case whose fix nobody
will ship — already fixed elsewhere, out of scope, a false positive found
after acceptance — is settled here with the path that says why. The pipeline
closes what it can see (an abandoned attempt, a cancelled draft, a healed
verdict); this is for everything a person decides out of band, and it was the
gap that left accepted cases with no terminal state at all.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from embry0.api.deps import ensure_admin, get_healing_cases_repo, request_actor
from embry0.api.repo_allowlist import enforce_repo_allowlist
from embry0.api.schemas import RunWithRequest
from embry0.api.schemas.cases import CaseDetailResponse, CaseListResponse, CaseSummary
from embry0.api.trigger_context import current_linear_sync, linear_trigger_context
from embry0.api.v1.run_with import checked_run_with
from embry0.audit.helpers import emit_audit
from embry0.services.healing.lifecycle import Origin, ResolutionPath, Stage, Verdict
from embry0.services.run_with import RunWith
from embry0.storage.repositories.healing_cases import HealingCasesRepository
from embry0.storage.repositories.jobs import StatusTransitionConflict
from embry0.storage.schemas import IssueStatus
from embry0.trackers.dispatch import create_tracked_issue, dispatch_open_issue
from embry0.trackers.models import TrackerIssue

logger = structlog.get_logger(__name__)

router = APIRouter()


def encode_cursor(created_at: datetime, case_id: str) -> str:
    """Opaque keyset position: the ``(created_at, id)`` of a page's last row."""
    return base64.urlsafe_b64encode(json.dumps([created_at.isoformat(), case_id]).encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw_created_at, case_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        created_at = datetime.fromisoformat(raw_created_at)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="cursor is not one this endpoint issued") from exc
    if created_at.tzinfo is None:
        # Issued cursors are always timezone-aware; a naive one would be
        # compared against timestamptz in the server's zone, silently.
        raise HTTPException(status_code=400, detail="cursor is not one this endpoint issued")
    return created_at, str(case_id)


@router.get("/cases")
async def list_cases(
    repo: str | None = None,
    stage: Stage | None = None,
    origin: Origin | None = None,
    resolution_path: ResolutionPath | None = None,
    healed_verdict: Verdict | None = None,
    fix_deployed: bool | None = None,
    open_only: bool = False,
    awaiting_decision: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    cases: HealingCasesRepository = Depends(get_healing_cases_repo),
) -> CaseListResponse:
    """Cases newest first, keyset-paginated: pass a page's ``next_cursor``
    back as ``cursor`` for the next one (None on the last page). A walk never
    repeats or drops a case, even while new ones are opened. ``open_only``
    drops closed cases; ``total`` counts every case matching the filters.

    ``awaiting_decision`` is "what is waiting for a person": watcher drafts
    with a ticket that nobody has accepted or rejected, findings a
    ``not_healed`` verdict sent back for a retry nobody has accepted yet,
    and organization needs (origin ``org``) waiting for a person.
    ``open_only`` is not that — it also returns alerts, findings with no
    ticket yet, and every merged, deployed and verified case, none of which
    anyone can act on here.

    To ask "did a fix ship?" filter on ``fix_deployed``, not on ``stage``:
    ``verified`` and ``closed`` also hold cases that healed with no fix."""
    rows, total = await cases.list_page(
        repo=repo,
        stage=stage.value if stage else None,
        origin=origin.value if origin else None,
        resolution_path=resolution_path.value if resolution_path else None,
        healed_verdict=healed_verdict.value if healed_verdict else None,
        fix_deployed=fix_deployed,
        open_only=open_only,
        awaiting_decision=awaiting_decision,
        # One extra row tells a full last page from a page with more behind it.
        limit=limit + 1,
        after=decode_cursor(cursor) if cursor else None,
    )
    page = rows[:limit]
    next_cursor = encode_cursor(page[-1]["created_at"], page[-1]["id"]) if len(rows) > limit else None
    return CaseListResponse(
        cases=[CaseSummary.from_row(r) for r in page], total=total, limit=limit, next_cursor=next_cursor
    )


@router.get("/cases/{case_id}")
async def get_case(
    case_id: str,
    cases: HealingCasesRepository = Depends(get_healing_cases_repo),
) -> CaseDetailResponse:
    """One case with its linked jobs, every PR it had and its full history."""
    row = await cases.get_detail(case_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"case {case_id} not found")
    return CaseDetailResponse.from_row(row)


# ---- accepting a finding (#378) ---------------------------------------------


class AcceptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Who said yes. Lands on the job as its dispatcher (the dispatch rate
    #: limit buckets per human, #246) and in the audit row. Empty: the API
    #: principal.
    accepted_by: str = Field(default="", max_length=200)
    #: The model and key the job runs with (spec 2026-10-25); none is the usual run.
    run_with: RunWithRequest | None = None


class CloseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Why the case is settled. Required unless the case already carries one
    #: (a merge, or ``self_resolved`` from a healed verdict), which is kept.
    resolution_path: str | None = Field(default=None)
    #: Free text for the ledger; truncated at the service boundary.
    note: str | None = Field(default=None, max_length=2000)
    #: Who decided. Empty: the API principal.
    closed_by: str = Field(default="", max_length=200)


def _github_issues(request: Request) -> Any:
    """The client that reads the draft back from GitHub. Tests plant a fake
    on ``app.state``; production builds the real one, which routes per-owner
    tokens itself."""
    planted = getattr(request.app.state, "github_issues_client", None)
    if planted is not None:
        return planted
    from embry0.execution.github_client import GitHubClient

    return GitHubClient(
        default_token=getattr(request.app.state.config, "github_token", "") or "",
        credential_resolver=getattr(request.app.state, "credential_resolver", None),
    )


@router.post("/cases/{case_id}/close")
async def close_case(
    case_id: str,
    body: CloseBody,
    request: Request,
    cases: HealingCasesRepository = Depends(get_healing_cases_repo),
) -> dict[str, Any]:
    """Close a case a human has decided is settled (#410).

    The mirror of ``accept``. ``closed`` is terminal but not deaf: whatever
    arrives afterwards is kept as history, and a later regression opens a new
    case pointing at this one — so this never hides a recurrence.

    Refused — nothing written — when the case does not exist (404), is an
    organization need's case (origin ``org``) and the caller is not admin tier
    (403, as dismissing the need is), is already closed (409), or names no
    ``resolution_path`` and has none of its own (422). The path must be one
    the ledger knows; anything else is 422 rather than a row the database
    would reject at write time.
    """
    case = await cases.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if str(case.get("origin")) == Origin.ORG:
        # Closing it dismisses the organization need, which is admin work.
        await ensure_admin(request)
    if str(case.get("stage")) == Stage.CLOSED:
        raise HTTPException(status_code=409, detail="the case is already closed")

    existing_path = str(case.get("resolution_path") or "")
    requested = (body.resolution_path or "").strip()
    if requested:
        try:
            path: ResolutionPath | None = ResolutionPath(requested)
        except ValueError:
            known = ", ".join(sorted(p.value for p in ResolutionPath))
            raise HTTPException(
                status_code=422, detail=f"unknown resolution_path {requested!r} (one of: {known})"
            ) from None
    elif existing_path:
        path = None  # the service keeps the one the case already has
    else:
        known = ", ".join(sorted(p.value for p in ResolutionPath))
        raise HTTPException(
            status_code=422, detail=f"this case has no resolution_path yet, so one is required (one of: {known})"
        )

    service = getattr(request.app.state, "healing_cases", None)
    if service is None:
        raise HTTPException(status_code=503, detail="healing ledger not initialized")
    actor = body.closed_by.strip() or request_actor(request)
    # CaseResult IS the case row (a dict subclass) plus how the write landed.
    closed = await service.close(
        case_id,
        resolution_path=path,
        note=body.note,
        event_key=f"api-close:{case_id}",
    )

    config = request.app.state.config
    await emit_audit(
        getattr(request.app.state, "db", None),
        "case_closed",
        actor=request_actor(request),
        details={
            "case_id": case_id,
            "repo": str(case.get("repo") or ""),
            "tracker_key": str(case.get("tracker_key") or ""),
            "resolution_path": str(closed.get("resolution_path") or ""),
            "closed_by": actor,
        },
        audit_log_path=getattr(config, "audit_log_path", None),
    )
    logger.info("case_closed", case_id=case_id, resolution_path=closed.get("resolution_path"), closed_by=actor)
    return {
        "case_id": case_id,
        "stage": str(closed.get("stage") or ""),
        "resolution_path": str(closed.get("resolution_path") or ""),
        "closed_by": actor,
        "outcome": str(getattr(closed, "outcome", "")),
    }


async def _accept_github(
    request: Request, case: dict[str, Any], actor: str, run_with: RunWith | None = None
) -> tuple[str, str]:
    """Dispatch a GitHub draft: read it back, mirror it as an embry0 issue,
    hand it to the executor — the label webhook's own steps. Returns
    ``(issue_id, job_id)``."""
    repo, tracker, key = str(case.get("repo") or ""), "github", str(case.get("tracker_key") or "")
    try:
        number = int(key)
    except ValueError:
        raise HTTPException(status_code=409, detail=f"{key!r} is not a GitHub issue number") from None
    fetched = await _github_issues(request).get_issue(repo, number)
    if fetched is None:
        raise HTTPException(status_code=502, detail=f"GitHub could not serve {repo}#{number}")
    if str(fetched.get("state") or "open") != "open":
        raise HTTPException(status_code=409, detail=f"{repo}#{number} is {fetched.get('state')} on GitHub")

    issues_repo = request.app.state.issues_repo
    executor = getattr(request.app.state, "issue_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="executor not initialized")
    existing = await issues_repo.get_by_tracker_ref(tracker, repo, key)
    if existing is None:
        issue_id = await create_tracked_issue(
            issues_repo,
            TrackerIssue(
                tracker=tracker,
                key=key,
                title=str(fetched.get("title") or case.get("title") or ""),
                body=str(fetched.get("body") or ""),
                url=str(fetched.get("html_url") or case.get("tracker_url") or ""),
                labels=[str(lbl.get("name")) for lbl in (fetched.get("labels") or []) if isinstance(lbl, dict)],
                repo=repo,
                state=str(fetched.get("state") or "open"),
            ),
            created_by="case-accept",
        )
        status = str(IssueStatus.OPEN)
    else:
        issue_id, status = str(existing["id"]), str(existing.get("status") or "")
        if status != IssueStatus.OPEN:
            raise HTTPException(
                status_code=409, detail=f"issue {issue_id} is {status}: a job is already active or it is closed"
            )

    job_id = await dispatch_open_issue(
        issue_id, status, issues_repo, executor, tracker=tracker, reason="case_accepted", actor=actor, run_with=run_with
    )
    if job_id is None:
        raise HTTPException(status_code=502, detail="the dispatch failed; the issue was returned to open, accept again")
    return issue_id, job_id


#: HTTP status for a Linear dispatch that was refused, by its ``skip`` code
#: (the part before any ``:``). Anything unlisted is a 409: the ticket is in
#: a state that does not take a job.
_LINEAR_REFUSAL_STATUS = {
    "cap": 429,
    "budget": 429,
    "label_add_failed": 502,
    "dispatch_error": 502,
    # The label was confirmed and the ticket then read back without it.
    "no_trigger_label": 502,
}


def _linear_refusal(key: str, result: dict[str, Any]) -> HTTPException:
    """Why a Linear draft was not dispatched, as the error the person reads.

    Says what happened to the ticket as well as why: a refusal after the
    label went on must not read like one where nothing was touched, and a
    label that could not be taken off again is the caller's to clean up.
    """
    skip = str(result.get("skip") or "")
    if result.get("action") == "issue_created_dispatch_failed":
        status, why = 502, "the dispatch failed and the issue was returned to open"
    else:
        status = _LINEAR_REFUSAL_STATUS.get(skip.split(":", 1)[0], 409)
        why = str(result.get("detail") or result.get("reason") or skip or "the dispatch was refused")
    label = str(result.get("label") or "untouched")
    name = str(result.get("label_name") or "trigger")
    if label == "removed":
        after = f" The `{name}` label was added to {key} and taken off again; no job was started."
    elif label == "left":
        after = (
            f" The `{name}` label was added to {key} and could NOT be taken off again:"
            f" the ticket is left labelled with no job. Remove the label in Linear."
        )
    elif label == "kept":
        after = f" The `{name}` label is on {key}; this acceptance started no job of its own."
    elif label == "unconfirmed":
        after = " No job was started."
    else:
        after = " Nothing was changed in Linear."
    return HTTPException(status_code=status, detail=f"{key} was not dispatched: {why.rstrip('.')}.{after}")


async def _accept_linear(
    request: Request, case: dict[str, Any], actor: str, run_with: RunWith | None = None
) -> tuple[str, str]:
    """Dispatch a Linear draft: put the trigger label on the ticket for the
    person, then dispatch through the Linear trigger path (#460).

    The label is not optional — that path refuses a ticket without it, and it
    is what shows in Linear that the ticket was accepted. Everything else is
    :meth:`LinearSyncService.label_and_dispatch`: this function only builds
    the context the label webhook builds and words the outcome. A draft whose
    Linear project resolves to another repo than the case's is refused before
    the ticket is touched — the job would run elsewhere and never be linked.
    """
    repo, key = str(case.get("repo") or ""), str(case.get("tracker_key") or "")
    linear_sync = await current_linear_sync(request)
    if linear_sync is None:
        raise HTTPException(status_code=503, detail="the Linear integration is not configured")
    if getattr(request.app.state, "issue_executor", None) is None:
        raise HTTPException(status_code=503, detail="executor not initialized")
    ctx = await linear_trigger_context(request, actor=actor)
    ctx.run_with = run_with
    try:
        result = await linear_sync.label_and_dispatch(key, ctx, created_by="case-accept", expected_repo=repo)
    except (ValueError, httpx.HTTPError) as exc:
        # The first read of the ticket: nothing has been written anywhere yet.
        logger.warning("case_accept_linear_unreadable", tracker_key=key, error=str(exc))
        raise HTTPException(status_code=502, detail=f"Linear could not serve {key}") from exc
    job_id = str(result.get("job_id") or "")
    if result.get("action") != "dispatched" or not job_id:
        raise _linear_refusal(key, result)
    return str(result.get("issue_id") or ""), job_id


async def _accept_retry(
    request: Request, case: dict[str, Any], actor: str, run_with: RunWith | None = None
) -> tuple[str, str]:
    """Dispatch the retry of a case a ``not_healed`` verdict sent back (#658).

    Such a case already links the embry0 issue its failed attempt ran on,
    and that issue was closed when the attempt's PR merged. Its tracker's
    own path refuses it for good reasons: a label never repeats delivered
    work (``trackers.redispatch``), and the ticket itself is often finished
    by then. Accepting the case is the person's explicit "try again", so the
    retry is dispatched here, on that same issue — reopened, then handed to
    the executor like a retry from the dashboard. The executor links the job
    to the case, which accepts it; the ticket is not touched. Returns
    ``(issue_id, job_id)``.

    Only a ``closed`` issue is reopened. One that is ``cancelled`` was
    stopped by a person, and one in any other status has a job on it.
    """
    issue_id = str(case.get("issue_id") or "")
    issues_repo = request.app.state.issues_repo
    executor = getattr(request.app.state, "issue_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="executor not initialized")
    issue = await issues_repo.get(issue_id)
    if issue is None:
        raise HTTPException(status_code=409, detail=f"the case's issue {issue_id} no longer exists")
    status = str(issue.get("status") or "")
    if status == IssueStatus.CLOSED:
        try:
            await issues_repo.update(issue_id, status=IssueStatus.OPEN)
        except (ValueError, StatusTransitionConflict):
            raise HTTPException(
                status_code=409, detail=f"issue {issue_id} changed while it was being reopened; accept again"
            ) from None
        status = IssueStatus.OPEN
    if status != IssueStatus.OPEN:
        raise HTTPException(
            status_code=409, detail=f"issue {issue_id} is {status}: a job is already active or it was cancelled"
        )

    job_id = await dispatch_open_issue(
        issue_id,
        status,
        issues_repo,
        executor,
        tracker=str(case.get("tracker") or ""),
        reason="case_retry_accepted",
        actor=actor,
        run_with=run_with,
    )
    if job_id is None:
        raise HTTPException(status_code=502, detail="the dispatch failed; the issue was returned to open, accept again")
    return issue_id, job_id


#: How a draft is dispatched, per tracker: ``(request, case, actor, run_with)`` →
#: ``(issue_id, job_id)``, or an HTTPException that says what, if anything,
#: was left behind.
#: A tracker that is not here keeps its label: GitLab drafts are still
#: accepted in GitLab (#460 left it out). Adding it is one more entry — put
#: the label on through the GitLab client, dispatch through ``gitlab_sync``.
_ACCEPTORS: dict[str, Callable[..., Awaitable[tuple[str, str]]]] = {
    "github": _accept_github,
    "linear": _accept_linear,
}


@router.post("/cases/{case_id}/accept", status_code=202)
async def accept_case(
    case_id: str,
    body: AcceptBody,
    request: Request,
    cases: HealingCasesRepository = Depends(get_healing_cases_repo),
) -> dict[str, Any]:
    """Accept a watcher finding: dispatch the pipeline on its draft ticket.

    What adding the trigger label in the tracker does, from here. A GitHub
    draft is dispatched as its label webhook would have; a Linear draft gets
    the trigger label put on the ticket and is dispatched by the Linear
    trigger path itself, so Linear shows the acceptance and the label
    webhook that follows finds the ticket already dispatched. Either way the
    case is linked and accepted by the job, and a PR, a merge and a deploy
    advance it from there. A GitLab draft is not accepted here yet: 409, add
    the label in GitLab.

    A case a ``not_healed`` verdict sent back to ``tracked`` already links
    the issue of its failed attempt. Accepting it dispatches the retry on
    that issue, whatever the tracker (``_accept_retry``): the tracker's own
    path would refuse delivered work.

    Refused — no job, and no label left on the ticket — unless the case is a
    watcher finding at stage ``tracked``, its draft is in a tracker this
    route dispatches (a retry needs none), and its repo is a stack this
    orchestrator watches and has enabled. The last condition is what lets a
    narrowly scoped key (the chat bot's) hold this route: it can accept this
    deployment's own findings and nothing else. The one exception to "no
    label left" is said in the error itself: a label that went on and could
    not be removed.
    """
    case = await cases.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    repo = str(case.get("repo") or "")
    tracker = str(case.get("tracker") or "")
    key = str(case.get("tracker_key") or "")
    await enforce_repo_allowlist(request, repo, action="accept a finding")
    run_with = await checked_run_with(request, body.run_with, repo)

    if str(case.get("origin")) != Origin.WATCHER:
        raise HTTPException(status_code=409, detail=f"not a watcher finding (origin {case.get('origin')})")
    stage = str(case.get("stage"))
    if stage != Stage.TRACKED:
        if stage in (Stage.DETECTED,):
            raise HTTPException(status_code=409, detail="no draft has been filed for this finding yet")
        if stage == Stage.CLOSED:
            raise HTTPException(status_code=409, detail="the case is closed")
        raise HTTPException(status_code=409, detail=f"already accepted (stage {stage})")
    # At `tracked` a case links an issue only when a reopen sent it back
    # there: every other way of linking one accepts the case.
    retry = bool(case.get("issue_id"))
    acceptor = _accept_retry if retry else _ACCEPTORS.get(tracker)
    if acceptor is None:
        raise HTTPException(
            status_code=409,
            detail=f"a {tracker or 'trackerless'} draft is accepted in its tracker: add the trigger label to {key or 'the ticket'}",
        )
    if not key and not retry:
        raise HTTPException(status_code=409, detail="the case names no draft ticket")

    stacks = getattr(request.app.state, "watcher_stacks_repo", None)
    if stacks is None:
        raise HTTPException(status_code=503, detail="watcher storage not initialized")
    stack = await stacks.get(repo)
    if stack is None or not stack.enabled:
        raise HTTPException(status_code=403, detail=f"{repo} is not a stack this embry0 watches")

    actor = body.accepted_by.strip() or request_actor(request)
    issue_id, job_id = await acceptor(request, dict(case), actor, run_with)

    config = request.app.state.config
    await emit_audit(
        getattr(request.app.state, "db", None),
        "case_accepted",
        actor=request_actor(request),
        details={
            "case_id": case_id,
            "repo": repo,
            "tracker_key": key,
            "issue_id": issue_id,
            "job_id": job_id,
            "accepted_by": actor,
            "retry": retry,
        },
        audit_log_path=getattr(config, "audit_log_path", None),
    )
    logger.info(
        "case_accepted", case_id=case_id, repo=repo, tracker_key=key, issue_id=issue_id, job_id=job_id, retry=retry
    )
    return {
        "case_id": case_id,
        "repo": repo,
        "tracker": tracker,
        "tracker_key": key,
        "issue_id": issue_id,
        "job_id": job_id,
        "accepted_by": actor,
        "status": "dispatched",
    }
