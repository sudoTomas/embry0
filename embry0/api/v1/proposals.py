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
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from embry0.api.deps import get_healing_cases_repo, get_issue_executor, get_issues_repo, request_actor
from embry0.api.repo_allowlist import hide_unless_covered, key_covers
from embry0.api.schemas import RunWithRequest
from embry0.api.schemas.person_name import PersonName
from embry0.api.v1.run_with import checked_run_with
from embry0.audit.helpers import emit_audit
from embry0.services.leads import LEAD_KINDS
from embry0.services.owners import Owners, repository_owners
from embry0.services.proposal_briefs import held_by, with_briefs
from embry0.services.proposals import (
    ACTOR,
    CODEOWNERS_PATHS_ON,
    KINDS,
    Proposal,
    case_proposal,
    finding_proposals,
    finding_task,
    for_owner,
    issue_proposal,
    lead_proposal,
    read_codeowners,
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


class _NotRead(Exception):
    """A repository whose ``CODEOWNERS`` embry0 does not read: its owners are not known, which is not "nobody owns
    it". Not a failure: the answer is kept as a read's is."""


def _repo_reader(request: Request) -> Any:
    """Reads a file of a repository's default branch with its organization's own credential, on its forge (GitHub's
    contents, GitLab's raw file); None when the file is not there. Raises :class:`_NotRead` for a repository it does
    not read: its organization not one embry0 holds (or one removed) on GitHub or GitLab, or a repository on another forge than its
    organization's (so no repository of the same path on the other forge is read). Raises when the forge could not
    be read (``forge_files.ForgeReadFailed``, a credential or network failure): "could not read" is not "no
    file"."""
    from embry0.services.forge_files import read_contents, read_gitlab_file

    organizations = getattr(request.app.state, "organizations_repo", None)
    held: dict[str, Any] = {}
    clients: dict[str, Any] = {}

    async def read(repo: str, path: str, forge: str) -> str | None:
        from embry0.api.v1.organizations import org_own_forge_client

        owner = repo.split("/", 1)[0]
        if owner not in held:
            held[owner] = await organizations.get(owner) if organizations is not None else None
        org = held[owner]
        if org is None:
            logger.info("codeowners_not_read_unknown_organization", repo=repo)
            raise _NotRead(repo)
        if org.status == "removed":
            # Removed is as good as unknown; a paused organization is still read.
            logger.info("codeowners_not_read_removed_organization", repo=repo)
            raise _NotRead(repo)
        if org.forge not in ("github", "gitlab"):
            logger.info("codeowners_not_read_unsupported_forge", repo=repo, organization_forge=org.forge)
            raise _NotRead(repo)
        if org.forge != forge:
            logger.info("codeowners_not_read_other_forge", repo=repo, repo_forge=forge, organization_forge=org.forge)
            raise _NotRead(repo)
        if owner not in clients:
            clients[owner] = await org_own_forge_client(request, org)
        if forge == "gitlab":
            return await read_gitlab_file(clients[owner], repo, path)
        return await read_contents(clients[owner].get, repo, path)

    return read


async def _codeowners(request: Request, repos: set[str]) -> dict[str, list[str] | None]:
    """Each repository's CODEOWNERS owners for every file, read on its forge with its organization's credential, in
    that forge's order of places and syntax (:func:`embry0.services.proposals.read_codeowners`), and cached until
    it expires or the repository's or its organization's registration is written (``registrations``).

    A repository with no file at any place has none (``[]``). One whose owners are not known is unread (None),
    which is not "nobody": kept as a read's answer is when embry0 does not read its file (its forge named nowhere,
    its organization not one embry0 holds, or a repository on another forge than its organization's), and not kept
    when its file or its forge could not be read (a refused or failed read, not "not there"), so the next read
    tries again. An entry left out (a team or group, a role, an email address) is logged once per read, with why.
    """
    from embry0.storage.repositories import registrations

    cache: dict[str, tuple[float, list[str] | None, int]] = request.app.state.__dict__.setdefault(
        "codeowners_cache", {}
    )
    now = time.monotonic()
    # Before any lookup: a registration written while this reads makes the next read read again.
    filled_at = registrations.count()

    def fresh(repo: str) -> bool:
        """Kept, and neither the repository's registration nor its organization's written since (#181)."""
        expires, _owners, at = cache[repo]
        return (
            expires >= now
            and not registrations.changed_since("repo", repo, at)
            and not registrations.changed_since("organization", repo.split("/", 1)[0], at)
        )

    wanted = [r for r in repos if r not in cache or not fresh(r)]
    forges, unread = await _forges(request, set(wanted)) if wanted else ({}, set())
    reader = _repo_reader(request)
    gate = asyncio.Semaphore(_CODEOWNERS_READS)

    async def read(repo: str) -> None:
        if repo.lower() in unread:
            return
        owners: list[str] | None = []
        forge = forges.get(repo.lower())
        async with gate:
            try:
                places = CODEOWNERS_PATHS_ON.get(forge or "", ())
                if forge is None:
                    # Nothing names where it lives (no registration, no organization, no default forge): not
                    # "nothing to read", but not known.
                    logger.info("codeowners_not_read_unknown_forge", repo=repo)
                    raise _NotRead(repo)
                if not places:
                    logger.info("codeowners_not_read_unsupported_forge", repo=repo, repo_forge=forge)
                    raise _NotRead(repo)
                for path in places:
                    text = await reader(repo, path, forge)
                    if text is not None:
                        found = read_codeowners(text, forge)
                        owners = list(found.owners)
                        if found.skipped:
                            logger.info(
                                "codeowners_entries_skipped",
                                repo=repo,
                                path=path,
                                skipped=[f"{entry}: {why}" for entry, why in found.skipped],
                            )
                        break
            except _NotRead:
                owners = None
            except Exception:  # noqa: BLE001 - unread, not "no owners", and not cached
                logger.warning("codeowners_unread", repo=repo, exc_info=True)
                unread.add(repo.lower())
                return
        cache[repo] = (now + CODEOWNERS_TTL_SECONDS, owners, filled_at)

    await asyncio.gather(*(read(r) for r in wanted))
    found: dict[str, list[str] | None] = {r: cache[r][1] for r in repos if r in cache}
    return found | {r: None for r in wanted if r.lower() in unread}


async def _areas(request: Request, repos: set[str]) -> dict[str, list[Any]]:
    """Each repository's areas as embry0 keeps them (``app.state.areas``), keyed lower-cased; one query."""
    store = getattr(request.app.state, "areas", None)
    if store is None:
        return {}
    return dict(await store.areas_of(repos))


async def _forges(request: Request, repos: set[str]) -> tuple[dict[str, str | None], set[str]]:
    """Each repository's own forge, keyed lower-cased: its registration's; for one nobody registered, its
    organization's when embry0 holds the organization, else the configured default (None when nothing says, and
    then it has no ``CODEOWNERS`` owners). Its ``CODEOWNERS`` is read only when that is its organization's forge
    too, so no repository of the same path on another forge is read (#181). With no registry, GitHub, as before
    forges were named.

    The second value: the repositories whose forge could not be looked up (the registry or the organizations
    failed), whose owners are unread, not none."""
    registry = getattr(request.app.state, "repos_repo", None)
    organizations = getattr(request.app.state, "organizations_repo", None)
    names = sorted({r.lower() for r in repos})
    if registry is None:
        return dict.fromkeys(names, "github"), set()
    unread: set[str] = set()

    async def forge_of(repo: str) -> str | None:
        try:
            if await registry.registered_as(repo) is None and organizations is not None:
                org = await organizations.get(repo.split("/", 1)[0])
                if org is not None and org.forge:
                    return str(org.forge)
            forge = await registry.forge_or_none(repo)
        except Exception:  # noqa: BLE001 - not looked up is not "no forge": its owners are unread
            logger.warning("owners_forge_unread", repo=repo, exc_info=True)
            unread.add(repo)
            return None
        return str(forge) if forge else None

    found = dict(zip(names, await asyncio.gather(*(forge_of(r) for r in names)), strict=True))
    return found, unread


async def owned_by(request: Request, proposals: list[Proposal], owner: str | None) -> list[Proposal]:
    """``proposals`` ``owner`` owns (:func:`embry0.services.proposals.for_owner`), each bare owner read on its
    repository's organization's forge (``owner_people.forges_of``, as asks and who may decide read it)."""
    if not owner:
        return proposals
    from embry0.services.owner_people import forges_of

    state = request.app.state
    forges = await forges_of(
        getattr(state, "organizations_repo", None), getattr(state, "repos_repo", None), {p.repo for p in proposals}
    )
    return for_owner(proposals, owner, forges)


async def owner_sources(request: Request, repos: set[str]) -> tuple[dict[str, Owners], dict[str, list[Any]]]:
    """Each of ``repos``' own owners (preference, else ``CODEOWNERS``) and its areas, both keyed lower-cased, each
    owner as written (``github:<login>``, ``gitlab:<username>``, or bare on the organization's forge, #181): what
    :func:`embry0.services.owners.owners_for` reads for the feed and the decisions alike."""
    proposals_repo = _state(request, "proposals_repo")
    preferences = await proposals_repo.owners()
    unowned = {r.lower() for r in repos} - set(preferences)
    codeowners, areas = await asyncio.gather(_codeowners(request, unowned), _areas(request, repos))
    return repository_owners(repos, preferences, codeowners), areas


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
    # The repository first: a 503 raised while the gather's arguments are built
    # would leave open_proposals' coroutine created and never awaited.
    _state(request, "proposals_repo")
    proposals = await open_proposals(request.app.state)
    repository, areas = await owner_sources(request, {p.repo.lower() for p in proposals})
    return sorted(with_owners(proposals, repository, areas), key=lambda p: p.created_at, reverse=True)


#: What reading a proposal that is not open answers, and one in a repository the caller's key does not cover.
_NOT_OPEN = "no open proposal with that id: answered, moved on, or never asked"


async def _open(request: Request, proposal_id: str) -> Proposal:
    found = next((p for p in await feed(request) if p.id == proposal_id), None)
    if found is None:
        raise not_open()
    return found


def readable(request: Request, proposals: Iterable[Proposal]) -> list[Proposal]:
    """The proposals in repositories the caller's key covers: every list of offered work keeps only these."""
    return [p for p in proposals if key_covers(request, p.repo)]


def not_open() -> HTTPException:
    """What reading or answering a proposal that is not open answers."""
    return HTTPException(status_code=404, detail=_NOT_OPEN)


async def readable_proposal(request: Request, proposal_id: str, *, action: str | None = None) -> Proposal:
    """One open proposal the caller may read or, naming ``action``, answer. One in a repository the caller's key
    does not cover answers as one that is not open does, so neither tells whether it exists; an action's
    refusal is logged and audited (:func:`~embry0.api.repo_allowlist.hide_unless_covered`)."""
    found = await _open(request, proposal_id)
    await hide_unless_covered(request, found.repo, not_open(), action=action)
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
        default=False,
        description=(
            "Also the proposals a chat does not ask: backlog tickets below the readiness floor (leads.ask_min), "
            "and leads of a severity leads.ask_severities does not ask"
        ),
    ),
) -> dict[str, Any]:
    """The proposals still asked, newest first: what an assistant reads to message owners."""
    if kind is not None and kind not in KINDS:
        raise HTTPException(status_code=422, detail=f"kind is one of {', '.join(KINDS)}")
    proposals = await owned_by(request, readable(request, await feed(request)), owner)
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
    return (await _shown(request, [await readable_proposal(request, proposal_id)]))[0]


async def _start_fix(request: Request, proposal: Proposal, run_with: RunWith | None = None) -> str:
    """A context-audit job narrowed to the finding's file, opening a pull request: the owner asked for this one.

    Its task's command lists the instruction files the repository reads now (the settings document's
    ``context_audit`` section, its organization's layer and its own over the deployment's), as the
    schedule's audits of it do.
    """
    from embry0.api.budget import unattended_cap
    from embry0.api.v1.organizations import github_only, org_forge_client
    from embry0.services.context_audit import RepoContext, read_head, start_context_audit_job
    from embry0.settings.sections.context_audit import CONTEXT_AUDIT, file_rules

    config = request.app.state.config
    org = await _state(request, "organizations_repo").get(proposal.repo.split("/", 1)[0])
    if org is None:
        raise HTTPException(status_code=409, detail=f"no registered organization holds {proposal.repo}'s credential")
    github_only(org, "fixing a finding")
    head = await read_head(await org_forge_client(request, org), proposal.repo)
    if head is None:
        raise HTTPException(status_code=409, detail=f"{proposal.repo} has no commit to fix")
    executor = getattr(request.app.state, "issue_executor", None)
    refusal = await executor.dispatch_refusal(actor=ACTOR) if executor is not None else None
    if refusal is not None:
        raise HTTPException(status_code=429, detail=refusal.public_message)
    budget_usd = await unattended_cap(request, proposal.repo, "fix_job_usd")
    store: SettingsStore | None = getattr(request.app.state, "settings", None)
    if store is None:
        raise HTTPException(status_code=503, detail="settings storage not initialized")
    rules = file_rules((await store.snapshot()).get(CONTEXT_AUDIT, repo=proposal.repo))
    context = RepoContext(proposal.repo, head[0], (str(proposal.extra["path"]),), f"{ACTOR}:{proposal.id}", rules=rules)
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
    proposal = await readable_proposal(request, proposal_id, action="accept a proposal")
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
    proposal = await readable_proposal(request, proposal_id, action="decline a proposal")
    return await _answer(request, proposal, "decline", body)


@router.post("/proposals/{proposal_id}/snooze")
async def snooze_proposal(proposal_id: str, body: SnoozeBody, request: Request) -> dict[str, Any]:
    """Not now: asked again once ``until`` passes."""
    until = body.until if body.until.tzinfo else body.until.replace(tzinfo=UTC)
    if until <= datetime.now(UTC):
        raise HTTPException(status_code=422, detail="until must be in the future")
    proposal = await readable_proposal(request, proposal_id, action="snooze a proposal")
    return await _answer(request, proposal, "snooze", body, until=until)
