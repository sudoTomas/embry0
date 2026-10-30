"""Owner proposals API (spec 2026-10-24): the feed of work a repository's owners could start, and their answers.

``GET /proposals`` lists what is still asked, each stamped with its
repository's owners; ``accept`` starts it through the path that already
exists for its kind (a case's acceptance, an issue's triage, a context-audit
fix job), ``decline`` and ``snooze`` record why not or until when. An
assistant holding a ``propose`` key may read this and answer, nothing else.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from embry0.api.deps import get_healing_cases_repo, get_issue_executor, get_issues_repo, request_actor
from embry0.api.repo_allowlist import enforce_repo_allowlist
from embry0.api.schemas import RunWithRequest
from embry0.api.schemas.person_name import PersonName
from embry0.api.v1.run_with import checked_run_with
from embry0.audit.helpers import emit_audit
from embry0.services.leads import LEAD_KINDS
from embry0.services.proposal_briefs import held_by, with_briefs
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
    lead_proposal,
    still_open,
    with_owners,
)
from embry0.services.run_with import RunWith
from embry0.settings.layers import PerRepository
from embry0.settings.sections.leads import LEADS, LeadsSettings
from embry0.settings.store import Scoped, SettingsStore

logger = structlog.get_logger(__name__)
router = APIRouter()

#: How long a repository's CODEOWNERS answer is kept: an owner added there shows within the hour.
CODEOWNERS_TTL_SECONDS = 3600.0
#: CODEOWNERS reads in flight at once.
_CODEOWNERS_READS = 8


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The GitHub login of the person who answered: the job's dispatcher and
    #: the answer's ``decided_by``. Never an ``embry0:`` actor's name.
    by: PersonName = Field(min_length=1, max_length=100)
    reason: str = Field(default="", max_length=2000)


class AcceptBody(AnswerBody):
    #: The model and key the job runs with (spec 2026-10-25); none is the usual run.
    run_with: RunWithRequest | None = None


class SnoozeBody(AnswerBody):
    until: datetime


def _state(request: Request, name: str) -> Any:
    value = getattr(request.app.state, name, None)
    if value is None:
        raise HTTPException(status_code=503, detail=f"{name} is not initialized")
    return value


def _repo_reader(request: Request) -> Any:
    """Reads a file of a repository's default branch with its organization's credential; None when it cannot."""
    from embry0.services.areas import read_contents

    organizations = getattr(request.app.state, "organizations_repo", None)
    clients: dict[str, Any] = {}

    async def read(repo: str, path: str) -> str | None:
        from embry0.api.v1.organizations import org_forge_client

        owner = repo.split("/", 1)[0]
        if owner not in clients:
            org = await organizations.get(owner) if organizations is not None else None
            clients[owner] = await org_forge_client(request, org) if org is not None else None
        client = clients[owner]
        return await read_contents(client.get, repo, path) if client is not None else None

    return read


async def _codeowners(request: Request, repos: set[str]) -> dict[str, list[str]]:
    """Each repository's CODEOWNERS users for every file, read with its organization's credential and cached.

    A repository whose organization embry0 does not hold, or whose file
    cannot be read, has none.
    """
    cache: dict[str, tuple[float, list[str]]] = request.app.state.__dict__.setdefault("codeowners_cache", {})
    now = time.monotonic()
    wanted = [r for r in repos if r not in cache or cache[r][0] < now]
    reader = _repo_reader(request)
    gate = asyncio.Semaphore(_CODEOWNERS_READS)

    async def read(repo: str) -> None:
        owners: list[str] = []
        async with gate:
            try:
                for path in CODEOWNERS_PATHS:
                    text = await reader(repo, path)
                    if text is not None:
                        owners = codeowners_for_all(text)
                        break
            except Exception:  # noqa: BLE001 - a repository we cannot read has no CODEOWNERS owners
                logger.info("codeowners_unread", repo=repo, exc_info=True)
        cache[repo] = (now + CODEOWNERS_TTL_SECONDS, owners)

    await asyncio.gather(*(read(r) for r in wanted))
    return {r: cache[r][1] for r in repos if r in cache}


async def _areas(request: Request, repos: set[str]) -> dict[str, list[Any]]:
    """Each repository's areas (``.embry0/areas.yml``), read with its organization's credential and cached."""
    from embry0.services.areas import AREAS

    reader = _repo_reader(request)
    gate = asyncio.Semaphore(_CODEOWNERS_READS)

    async def read(repo: str) -> tuple[str, list[Any]]:
        async with gate:
            return repo, await AREAS.areas(repo, reader)

    return dict(await asyncio.gather(*(read(r) for r in repos)))


async def open_proposals(state: Any) -> list[Proposal]:
    """Every proposal still asked, without its owners: what the feed and the brief writer read."""
    repo = getattr(state, "proposals_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="proposals_repo is not initialized")
    leads = getattr(state, "leads_repo", None)
    cases, issues, audits, decisions, open_leads = await asyncio.gather(
        repo.cases(), repo.issues(), repo.audits(), repo.decisions(), leads.open_leads() if leads else _none()
    )
    proposals = [case_proposal(r) for r in cases] + [issue_proposal(r) for r in issues]
    proposals += [lead_proposal(r) for r in open_leads]
    for audit in audits:
        proposals += finding_proposals(
            str(audit["repo"]), str(audit["job_id"]), audit["finished_at"], audit["result_summary"]
        )
    now = datetime.now(UTC)
    return [p for p in proposals if still_open(p.id, decisions, now)]


async def _none() -> list[dict[str, Any]]:
    return []


async def feed(request: Request) -> list[Proposal]:
    """Every proposal still asked, newest first, with its owners."""
    proposals, owners = await asyncio.gather(
        open_proposals(request.app.state), _state(request, "proposals_repo").owners()
    )
    repos = {p.repo.lower() for p in proposals}
    unowned = repos - set(owners)
    codeowners, areas = await asyncio.gather(_codeowners(request, unowned), _areas(request, repos))
    owners = {**owners, **codeowners}
    return sorted(with_owners(proposals, owners, areas), key=lambda p: p.created_at, reverse=True)


async def _open(request: Request, proposal_id: str) -> Proposal:
    found = next((p for p in await feed(request) if p.id == proposal_id), None)
    if found is None:
        raise HTTPException(status_code=404, detail="no open proposal with that id: answered, moved on, or never asked")
    return found


async def leads_policy(request: Request) -> Scoped[LeadsSettings]:
    """The settings document's ``leads`` section as it stands now, read per repository: whose
    ``ask_min`` holds a backlog ticket out of chat."""
    store: SettingsStore | None = getattr(request.app.state, "settings", None)
    if store is None:
        raise HTTPException(status_code=503, detail="settings storage not initialized")
    return (await store.snapshot()).scoped(LEADS)


async def _shown(
    request: Request, proposals: list[Proposal], leads: PerRepository[LeadsSettings] | None = None
) -> list[dict[str, Any]]:
    """The proposals as the API shows them: each with its brief (spec 2026-10-25) and whether it is held,
    by ``leads`` (read now when not given)."""
    briefs = getattr(request.app.state, "proposal_briefs_repo", None)
    stored: dict[str, Any] = {}
    if briefs is not None:
        try:
            stored = await briefs.all()
        except Exception:  # noqa: BLE001 - the feed never waits on briefs
            logger.warning("proposal_briefs_unread", exc_info=True)
    return with_briefs(proposals, stored, leads if leads is not None else await leads_policy(request))


@router.get("/proposals")
async def list_proposals(
    request: Request,
    owner: str | None = Query(default=None, description="A GitHub login: only the proposals it owns"),
    kind: str | None = Query(default=None, description=", ".join(KINDS)),
    since: datetime | None = Query(default=None, description="Only proposals asked after this time"),
    include_held: bool = Query(
        default=False, description="Also the backlog tickets below the readiness floor, which a chat does not ask"
    ),
) -> dict[str, Any]:
    """The proposals still asked, newest first: what an assistant reads to message owners."""
    if kind is not None and kind not in KINDS:
        raise HTTPException(status_code=422, detail=f"kind is one of {', '.join(KINDS)}")
    proposals = for_owner(await feed(request), owner)
    if kind is not None:
        proposals = [p for p in proposals if p.kind == kind]
    if since is not None:
        proposals = [p for p in proposals if p.created_at > since]
    leads = await leads_policy(request)
    if not include_held:
        proposals = [p for p in proposals if not held_by(p, leads)]
    shown = await _shown(request, proposals, leads)
    return {"proposals": shown, "count": len(shown)}


@router.get("/proposals/{proposal_id}")
async def get_proposal(proposal_id: str, request: Request) -> dict[str, Any]:
    return (await _shown(request, [await _open(request, proposal_id)]))[0]


async def _start_fix(request: Request, proposal: Proposal, run_with: RunWith | None = None) -> str:
    """A context-audit job narrowed to the finding's file, opening a pull request: the owner asked for this one."""
    from embry0.api.budget import unattended_cap
    from embry0.api.v1.organizations import org_forge_client
    from embry0.services.context_audit import RepoContext, read_head, start_context_audit_job

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
    budget_usd = await unattended_cap(request, proposal.repo, "fix_job_usd")
    try:
        return await start_context_audit_job(
            context,
            jobs=request.app.state.jobs_repo,
            executor=executor,
            templates=request.app.state.templates_repo,
            budget_usd=budget_usd,
            open_pr=True,
            db=getattr(request.app.state, "db", None),
            audit_log_path=getattr(config, "audit_log_path", None),
            task=finding_task(proposal.repo, head[0], proposal),
            dispatched_by=ACTOR,
            run_with=run_with,
        )
    except (LookupError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


#: A proposal answer as a decision's outcome (decisions, spec 2026-10-27).
_OUTCOMES = {"accept": "start", "decline": "decline", "snooze": "later"}


def decider(request: Request, said: str) -> str:
    """Who answered: the signed-in person the server knows, else the person a key names (a chat assistant)."""
    from embry0.api.session import request_session

    session = request_session(request)
    return str(session.email) if session is not None and session.email else said


async def _answer(
    request: Request,
    proposal: Proposal,
    decision: str,
    body: AnswerBody,
    *,
    until: datetime | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    from embry0.services.handoffs import settle

    body = body.model_copy(update={"by": decider(request, body.by)})
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
    # Whoever answers, a hand-off of this decision is decided (spec 2026-10-27).
    await settle(request.app.state, f"offer:{proposal.id}", _OUTCOMES.get(decision, decision), body.by)
    return {
        "proposal_id": proposal.id,
        "decision": decision,
        "by": body.by,
        "job_id": job_id,
        "until": until.isoformat() if until else None,
    }


async def record_lead_accept(request: Request, row: dict[str, Any], *, by: str, reason: str, job_id: str) -> None:
    """Record a lead's yes given somewhere other than the proposals routes (a knowledge map's send)."""
    await _answer(request, lead_proposal(row), "accept", AnswerBody(by=by, reason=reason[:2000]), job_id=job_id)


@router.post("/proposals/{proposal_id}/accept", status_code=202)
async def accept_proposal(proposal_id: str, body: AcceptBody, request: Request) -> dict[str, Any]:
    """Start it, through the path its kind already has; the answer is recorded only once the start worked."""
    proposal = await _open(request, proposal_id)
    await enforce_repo_allowlist(request, proposal.repo, action="accept a proposal")
    # Checked before anything starts; the case and issue paths check it again as their routes do.
    run_with = await checked_run_with(request, body.run_with, proposal.repo)
    native = proposal.id.split(":", 1)[1]
    if proposal.kind == "case":
        from embry0.api.v1.cases import AcceptBody as CaseAcceptBody
        from embry0.api.v1.cases import accept_case

        result = await accept_case(
            native,
            CaseAcceptBody(accepted_by=body.by, run_with=body.run_with),
            request,
            get_healing_cases_repo(request),
        )
        job_id = result.get("job_id")
    elif proposal.kind == "issue":
        from embry0.api.v1.issues import TriageBody, triage_issue

        dispatched = await triage_issue(
            native,
            request,
            get_issues_repo(request),
            get_issue_executor(request),
            TriageBody(run_with=body.run_with),
        )
        job_id = dispatched.job_id
    elif proposal.kind in LEAD_KINDS:
        from embry0.api.v1.leads import start_lead

        job_id = await start_lead(request, proposal.id, by=body.by, run_with=run_with)
    else:
        job_id = await _start_fix(request, proposal, run_with)
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
