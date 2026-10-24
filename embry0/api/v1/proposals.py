"""Owner proposals API (spec 2026-10-24): the feed of work a repository's owners could start, and their answers.

``GET /proposals`` lists what is still asked, each stamped with its
repository's owners; ``accept`` starts it through the path that already
exists for its kind (a case's acceptance, an issue's triage, a context-audit
fix job), ``decline`` and ``snooze`` record why not or until when. An
assistant holding a ``propose`` key may read this and answer, nothing else.
"""

from __future__ import annotations

import asyncio
import base64
import time
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from embry0.api.deps import get_healing_cases_repo, get_issue_executor, get_issues_repo, request_actor
from embry0.api.repo_allowlist import enforce_repo_allowlist
from embry0.audit.helpers import emit_audit
from embry0.services.proposals import (
    ACTOR,
    CODEOWNERS_PATHS,
    KINDS,
    Proposal,
    case_proposal,
    codeowners_for_all,
    finding_proposals,
    finding_task,
    for_owner,
    issue_proposal,
    still_open,
    with_owners,
)

logger = structlog.get_logger(__name__)
router = APIRouter()

#: How long a repository's CODEOWNERS answer is kept: an owner added there shows within the hour.
CODEOWNERS_TTL_SECONDS = 3600.0
#: CODEOWNERS reads in flight at once.
_CODEOWNERS_READS = 8


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The GitHub login of the person who answered.
    by: str = Field(min_length=1, max_length=100)
    reason: str = Field(default="", max_length=2000)


class SnoozeBody(AnswerBody):
    until: datetime


def _state(request: Request, name: str) -> Any:
    value = getattr(request.app.state, name, None)
    if value is None:
        raise HTTPException(status_code=503, detail=f"{name} is not initialized")
    return value


async def _codeowners(request: Request, repos: set[str]) -> dict[str, list[str]]:
    """Each repository's CODEOWNERS users for every file, read with its organization's credential and cached.

    A repository whose organization embry0 does not hold, or whose file
    cannot be read, has none.
    """
    cache: dict[str, tuple[float, list[str]]] = request.app.state.__dict__.setdefault("codeowners_cache", {})
    now = time.monotonic()
    wanted = [r for r in repos if r not in cache or cache[r][0] < now]
    organizations = getattr(request.app.state, "organizations_repo", None)
    gate = asyncio.Semaphore(_CODEOWNERS_READS)

    async def read(repo: str) -> None:
        owners: list[str] = []
        async with gate:
            try:
                from embry0.api.v1.organizations import org_forge_client

                org = await organizations.get(repo.split("/", 1)[0]) if organizations is not None else None
                if org is not None:
                    client = await org_forge_client(request, org)
                    for path in CODEOWNERS_PATHS:
                        resp = await client.get(f"/repos/{repo}/contents/{path}")
                        if resp.status_code != 200:
                            continue
                        body = resp.json()
                        text = base64.b64decode(body.get("content", "")).decode("utf-8", "replace")
                        owners = codeowners_for_all(text)
                        break
            except Exception:  # noqa: BLE001 - a repository we cannot read has no CODEOWNERS owners
                logger.info("codeowners_unread", repo=repo, exc_info=True)
        cache[repo] = (now + CODEOWNERS_TTL_SECONDS, owners)

    await asyncio.gather(*(read(r) for r in wanted))
    return {r: cache[r][1] for r in repos if r in cache}


async def feed(request: Request) -> list[Proposal]:
    """Every proposal still asked, newest first, with its owners."""
    repo = _state(request, "proposals_repo")
    cases, issues, audits, owners, decisions = await asyncio.gather(
        repo.cases(), repo.issues(), repo.audits(), repo.owners(), repo.decisions()
    )
    proposals = [case_proposal(r) for r in cases] + [issue_proposal(r) for r in issues]
    for audit in audits:
        proposals += finding_proposals(
            str(audit["repo"]), str(audit["job_id"]), audit["finished_at"], audit["result_summary"]
        )
    now = datetime.now(UTC)
    proposals = [p for p in proposals if still_open(p.id, decisions, now)]
    unowned = {p.repo.lower() for p in proposals} - set(owners)
    if unowned:
        owners = {**owners, **(await _codeowners(request, unowned))}
    return sorted(with_owners(proposals, owners), key=lambda p: p.created_at, reverse=True)


async def _open(request: Request, proposal_id: str) -> Proposal:
    found = next((p for p in await feed(request) if p.id == proposal_id), None)
    if found is None:
        raise HTTPException(status_code=404, detail="no open proposal with that id: answered, moved on, or never asked")
    return found


@router.get("/proposals")
async def list_proposals(
    request: Request,
    owner: str | None = Query(default=None, description="A GitHub login: only the proposals it owns"),
    kind: str | None = Query(default=None, description="case, issue or finding"),
    since: datetime | None = Query(default=None, description="Only proposals asked after this time"),
) -> dict[str, Any]:
    """The proposals still asked, newest first: what an assistant reads to message owners."""
    if kind is not None and kind not in KINDS:
        raise HTTPException(status_code=422, detail=f"kind is one of {', '.join(KINDS)}")
    proposals = for_owner(await feed(request), owner)
    if kind is not None:
        proposals = [p for p in proposals if p.kind == kind]
    if since is not None:
        proposals = [p for p in proposals if p.created_at > since]
    return {"proposals": [p.to_dict() for p in proposals], "count": len(proposals)}


@router.get("/proposals/{proposal_id}")
async def get_proposal(proposal_id: str, request: Request) -> dict[str, Any]:
    return (await _open(request, proposal_id)).to_dict()


async def _start_fix(request: Request, proposal: Proposal) -> str:
    """A context-audit job narrowed to the finding's file, opening a pull request: the owner asked for this one."""
    from embry0.api.v1.organizations import org_forge_client
    from embry0.services.context_audit import RepoContext, job_budget_usd, read_head, start_context_audit_job

    config = request.app.state.config
    org = await _state(request, "organizations_repo").get(proposal.repo.split("/", 1)[0])
    if org is None:
        raise HTTPException(status_code=409, detail=f"no registered organization holds {proposal.repo}'s credential")
    head = await read_head(await org_forge_client(request, org), proposal.repo)
    if head is None:
        raise HTTPException(status_code=409, detail=f"{proposal.repo} has no commit to fix")
    executor = getattr(request.app.state, "issue_executor", None)
    refusal = await executor.dispatch_refusal(actor=ACTOR) if executor is not None else None
    if refusal is not None:
        raise HTTPException(status_code=429, detail=refusal.public_message)
    context = RepoContext(proposal.repo, head[0], (str(proposal.extra["path"]),), f"{ACTOR}:{proposal.id}")
    try:
        return await start_context_audit_job(
            context,
            jobs=request.app.state.jobs_repo,
            executor=executor,
            templates=request.app.state.templates_repo,
            budget_usd=job_budget_usd(config),
            open_pr=True,
            db=getattr(request.app.state, "db", None),
            audit_log_path=getattr(config, "audit_log_path", None),
            task=finding_task(proposal.repo, head[0], proposal),
            dispatched_by=ACTOR,
        )
    except (LookupError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _answer(
    request: Request,
    proposal: Proposal,
    decision: str,
    body: AnswerBody,
    *,
    until: datetime | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    await _state(request, "proposals_repo").record(
        proposal_id=proposal.id,
        repo=proposal.repo,
        kind=proposal.kind,
        decision=decision,
        decided_by=body.by,
        reason=body.reason,
        until=until,
        job_id=job_id,
    )
    config = request.app.state.config
    await emit_audit(
        getattr(request.app.state, "db", None),
        "proposal.answered",
        actor=request_actor(request),
        details={
            "proposal_id": proposal.id,
            "repo": proposal.repo,
            "kind": proposal.kind,
            "decision": decision,
            "by": body.by,
            "job_id": job_id,
        },
        audit_log_path=getattr(config, "audit_log_path", None),
    )
    logger.info("proposal_answered", proposal_id=proposal.id, decision=decision, by=body.by, job_id=job_id)
    return {
        "proposal_id": proposal.id,
        "decision": decision,
        "by": body.by,
        "job_id": job_id,
        "until": until.isoformat() if until else None,
    }


@router.post("/proposals/{proposal_id}/accept", status_code=202)
async def accept_proposal(proposal_id: str, body: AnswerBody, request: Request) -> dict[str, Any]:
    """Start it, through the path its kind already has; the answer is recorded only once the start worked."""
    proposal = await _open(request, proposal_id)
    await enforce_repo_allowlist(request, proposal.repo, action="accept a proposal")
    native = proposal.id.split(":", 1)[1]
    if proposal.kind == "case":
        from embry0.api.v1.cases import AcceptBody, accept_case

        result = await accept_case(native, AcceptBody(accepted_by=body.by), request, get_healing_cases_repo(request))
        job_id = result.get("job_id")
    elif proposal.kind == "issue":
        from embry0.api.v1.issues import triage_issue

        dispatched = await triage_issue(native, request, get_issues_repo(request), get_issue_executor(request))
        job_id = dispatched.job_id
    else:
        job_id = await _start_fix(request, proposal)
    return await _answer(request, proposal, "accept", body, job_id=job_id)


@router.post("/proposals/{proposal_id}/decline")
async def decline_proposal(proposal_id: str, body: AnswerBody, request: Request) -> dict[str, Any]:
    """Not this: recorded with the reason, and not asked again."""
    proposal = await _open(request, proposal_id)
    await enforce_repo_allowlist(request, proposal.repo, action="decline a proposal")
    return await _answer(request, proposal, "decline", body)


@router.post("/proposals/{proposal_id}/snooze")
async def snooze_proposal(proposal_id: str, body: SnoozeBody, request: Request) -> dict[str, Any]:
    """Not now: asked again once ``until`` passes."""
    until = body.until if body.until.tzinfo else body.until.replace(tzinfo=UTC)
    if until <= datetime.now(UTC):
        raise HTTPException(status_code=422, detail="until must be in the future")
    proposal = await _open(request, proposal_id)
    await enforce_repo_allowlist(request, proposal.repo, action="snooze a proposal")
    return await _answer(request, proposal, "snooze", body, until=until)
