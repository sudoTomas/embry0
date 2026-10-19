"""Standards API (ADR 0014): hold an owner's versioned standards, set each repository's mode, start audits.

Reads are operator-tier like the rest of the registry. Writing a standard and
setting a repository's mode are admin-tier: they decide what embry0 holds every
repository under an owner to, and which repositories it may spend on. Starting
an audit is operator-tier like ``POST /jobs``, with the same repository
allowlist, and only for a repository an admin put in mode advise or enforce.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, cast

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from embry0.api.deps import request_actor, require_admin
from embry0.api.repo_allowlist import enforce_repo_allowlist
from embry0.api.schemas.repo_path import REPO_PATH_ERROR, REPO_PATH_PATTERN
from embry0.audit.helpers import emit_audit
from embry0.services import standards as standards_service
from embry0.services.standards import (
    AUDIT_TEMPLATES,
    KINDS,
    MAX_DOCUMENT_CHARS,
    MAX_REFERENCE_CHARS,
    MAX_REFERENCES,
    MODES,
    RepoStandardState,
    Standard,
)
from embry0.storage.repositories.jobs import TERMINAL_JOB_STATUSES
from embry0.storage.repositories.standards import RepoStandardStateRepository, StandardsRepository

logger = structlog.get_logger(__name__)
router = APIRouter()

Reference = Annotated[str, StringConstraints(min_length=1, max_length=MAX_REFERENCE_CHARS)]
ExceptionPath = Annotated[str, StringConstraints(min_length=1, max_length=500)]


class StandardBody(BaseModel):
    """A complete new version. Every field it leaves out is empty in that version, not carried over."""

    model_config = ConfigDict(extra="forbid")

    # ``schema`` would shadow a BaseModel attribute, so the field is named
    # ``schema_`` and read and written as ``schema``.
    schema_: dict[str, Any] = Field(
        alias="schema",
        description='the shape every standard of the kind shares: {"roles": {...}, "type_scale": {...}, "rules": [...]}',
    )
    values: dict[str, Any] = Field(description="this owner's fill of the roles: role name to value")
    prose: str = Field(default="", max_length=MAX_DOCUMENT_CHARS, description="the standard in words, markdown")
    references: list[Reference] = Field(
        default_factory=list,
        max_length=MAX_REFERENCES,
        description="repositories or URLs where the standard is held best",
    )


class StandardException(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: str = Field(min_length=1, max_length=200, description="the rule id or role the exception covers")
    reason: str = Field(min_length=1, max_length=1000)
    paths: list[ExceptionPath] = Field(
        default_factory=list, max_length=50, description="where it applies; empty means the whole repository"
    )


class RepoModeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["off", "advise", "enforce"]
    exceptions: list[StandardException] | None = Field(
        default=None, max_length=100, description="replaces the repository's exceptions; omitted keeps them"
    )


def _standards(request: Request) -> StandardsRepository:
    repository = getattr(request.app.state, "standards_repo", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="standards storage not initialized")
    return cast("StandardsRepository", repository)


def _states(request: Request) -> RepoStandardStateRepository:
    repository = getattr(request.app.state, "repo_standard_state_repo", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="standards storage not initialized")
    return cast("RepoStandardStateRepository", repository)


def _owner(raw: str) -> str:
    try:
        return standards_service.normalize_owner(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _kind(raw: str) -> str:
    try:
        return standards_service.check_kind(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


async def _registered_repo_under(request: Request, owner: str, repo: str) -> str:
    """The registry's spelling of ``repo``, which must sit under ``owner``: 422 when it does not, 404 when unregistered."""
    if not REPO_PATH_PATTERN.match(repo):
        raise HTTPException(status_code=422, detail=REPO_PATH_ERROR)
    if standards_service.owner_of(repo) != owner:
        raise HTTPException(
            status_code=422, detail=f"{repo} is not under {owner}: a standard applies to its owner's repositories"
        )
    row = await request.app.state.repos_repo.get(repo)
    if row is None:
        raise HTTPException(status_code=404, detail="repo not registered")
    return str(row["repo"])


async def _active_or_404(request: Request, owner: str, kind: str) -> Standard:
    standard = await _standards(request).get_active(owner, kind)
    if standard is None:
        raise HTTPException(status_code=404, detail=f"no active {kind} standard for {owner}")
    return standard


async def _audit(request: Request, action: str, details: dict[str, Any]) -> None:
    config = getattr(request.app.state, "config", None)
    await emit_audit(
        getattr(request.app.state, "db", None),
        action,
        actor=request_actor(request),
        details=details,
        audit_log_path=getattr(config, "audit_log_path", None),
    )


async def _state_or_default(request: Request, repo: str, kind: str) -> RepoStandardState:
    return await _states(request).get(repo, kind) or standards_service.default_state(repo, kind)


async def standards_for_repo(request: Request, repo: str) -> list[dict[str, Any]]:
    """One state per kind for ``GET /repos/{repo}``: the stored row, or mode off when there is none."""
    repository = getattr(request.app.state, "repo_standard_state_repo", None)
    stored = {s.kind: s for s in await repository.list_for_repo(repo)} if repository is not None else {}
    return [(stored.get(kind) or standards_service.default_state(repo, kind)).to_dict() for kind in KINDS]


@router.get("/standards/{owner}")
async def list_standards(owner: str, request: Request) -> dict[str, Any]:
    """The owner's active standard of each kind, and every repository under it with a stored mode."""
    owner = _owner(owner)
    active = await _standards(request).list_active(owner)
    states = await _states(request).list_for_owner(owner)
    return {
        "owner": owner,
        "kinds": list(KINDS),
        "modes": list(MODES),
        "standards": [s.to_dict() for s in active],
        "repos": [s.to_dict() for s in states],
    }


@router.get("/standards/{owner}/{kind}")
async def get_standard(owner: str, kind: str, request: Request) -> dict[str, Any]:
    """The active version of the owner's standard of ``kind``; 404 when none was written."""
    return (await _active_or_404(request, _owner(owner), _kind(kind))).to_dict()


@router.put("/standards/{owner}/{kind}", dependencies=[require_admin])
async def put_standard(owner: str, kind: str, body: StandardBody, request: Request) -> dict[str, Any]:
    """Store a new version and supersede the active one. The body is the whole version.

    Refused with 422 when the schema or the values are not objects, the
    schema holds a key other than roles, type_scale and rules, or a value
    names a role the schema does not declare.
    """
    owner, kind = _owner(owner), _kind(kind)
    problems = standards_service.validate_standard(body.schema_, body.values, body.prose, list(body.references))
    if problems:
        raise HTTPException(status_code=422, detail={"message": "the standard cannot be stored", "problems": problems})
    standard, superseded = await _standards(request).put(
        owner,
        kind,
        schema=body.schema_,
        values=body.values,
        prose=body.prose,
        references=list(body.references),
        created_by=request_actor(request),
    )
    await _audit(
        request,
        "standard.updated",
        {"owner": owner, "kind": kind, "version": standard.version, "superseded_version": superseded},
    )
    return standard.to_dict()


@router.get("/standards/{owner}/{kind}/versions")
async def list_standard_versions(owner: str, kind: str, request: Request) -> dict[str, Any]:
    """Every version of the owner's standard of ``kind``, newest first, with its status."""
    owner, kind = _owner(owner), _kind(kind)
    versions = await _standards(request).list_versions(owner, kind)
    return {"owner": owner, "kind": kind, "versions": [v.to_dict() for v in versions]}


@router.get("/standards/{owner}/{kind}/repos/{repo:path}")
async def get_repo_standard_state(owner: str, kind: str, repo: str, request: Request) -> dict[str, Any]:
    """The repository's mode, exceptions and newest audit under this standard; mode off when never set."""
    owner, kind = _owner(owner), _kind(kind)
    await _registered_repo_under(request, owner, repo)
    return (await _state_or_default(request, repo, kind)).to_dict()


@router.put("/standards/{owner}/{kind}/repos/{repo:path}", dependencies=[require_admin])
async def put_repo_standard_state(
    owner: str, kind: str, repo: str, body: RepoModeBody, request: Request
) -> dict[str, Any]:
    """Set the repository's mode (off, advise, enforce), and its exceptions when sent."""
    owner, kind = _owner(owner), _kind(kind)
    await _registered_repo_under(request, owner, repo)
    exceptions = [e.model_dump() for e in body.exceptions] if body.exceptions is not None else None
    state = await _states(request).set_mode(repo, kind, body.mode, exceptions=exceptions)
    await _audit(
        request,
        "standard.repo_mode_set",
        {
            "owner": owner,
            "kind": kind,
            "repo": state.repo,
            "mode": state.mode,
            "exceptions": len(state.exceptions),
        },
    )
    return state.to_dict()


async def _template_or_409(request: Request, kind: str) -> str:
    name = AUDIT_TEMPLATES[kind]
    templates = getattr(request.app.state, "templates_repo", None)
    rows = await templates.list_all() if templates is not None else []
    if not any(row.get("name") == name for row in rows):
        raise HTTPException(
            status_code=409,
            detail=f"no pipeline template named {name!r}: a {kind} audit runs on it, and it is seeded with the "
            "built-in agents",
        )
    return name


async def _unfinished_status(request: Request, job_id: str | None) -> str | None:
    """The job's status while it is pending or running; None when it finished, is gone, or there is none."""
    if not job_id:
        return None
    job = await request.app.state.jobs_repo.get(job_id)
    if job is None or job.get("status") in TERMINAL_JOB_STATUSES:
        return None
    return str(job.get("status"))


def _busy_409(repo: str, status: str, job_id: str | None) -> HTTPException:
    return HTTPException(status_code=409, detail=f"an audit of {repo} is already {status}: job {job_id}")


@router.post("/standards/{owner}/{kind}/audit/{repo:path}", status_code=202)
async def start_standard_audit(owner: str, kind: str, repo: str, request: Request) -> dict[str, Any]:
    """Start a read-only audit job of the repository against the active standard.

    The job runs on the kind's pipeline template (``design-audit`` for a design
    standard) and its task carries the whole active standard. When it
    completes, its report's ``Score: N`` line and findings table become the
    repository's ``score`` and ``findings_open``. Refused with 409 when the
    repository is in mode off, there is no active standard, the template is
    absent, or the repository's previous audit is still running, including
    one another request started at the same moment.
    """
    owner, kind = _owner(owner), _kind(kind)
    registered = await _registered_repo_under(request, owner, repo)
    await enforce_repo_allowlist(request, registered, action="start an audit")
    state = await _state_or_default(request, registered, kind)
    if state.mode == "off":
        raise HTTPException(
            status_code=409,
            detail=f"{registered} is in mode off for the {kind} standard: an admin sets advise or enforce with "
            f"PUT /api/v1/standards/{owner}/{kind}/repos/{registered} before it can be audited",
        )
    standard = await _standards(request).get_active(owner, kind)
    if standard is None:
        raise HTTPException(status_code=409, detail=f"no active {kind} standard for {owner} to audit against")
    template = await _template_or_409(request, kind)
    busy = await _unfinished_status(request, state.last_audit_job_id)
    if busy is not None:
        raise _busy_409(registered, busy, state.last_audit_job_id)
    executor = getattr(request.app.state, "issue_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="executor not initialized")

    task = standards_service.audit_task(standard, registered, state.exceptions)
    actor = request_actor(request)
    jobs = request.app.state.jobs_repo
    job_id: str = await jobs.create(
        repo=registered,
        task=task,
        context={"type": "git", "repo": registered},
        pipeline_template=template,
        dispatched_by=actor,
    )
    # Named on the state before the job runs, so its completion finds it. The
    # update is a compare-and-set over the newest audit read and checked
    # above, and holds only outside mode off. When it does not hold, another
    # request started an audit since the read (a double click, a retried tool
    # call) or an admin switched the mode off, and this job is cancelled
    # before it starts.
    if await _states(request).start_audit(registered, kind, job_id, expected_job_id=state.last_audit_job_id) is None:
        await executor.cancel_job(job_id, actor=actor)
        now = await _state_or_default(request, registered, kind)
        if now.mode == "off":
            raise HTTPException(
                status_code=409, detail=f"{registered} was switched to mode off; the audit was not started"
            )
        busy = await _unfinished_status(request, now.last_audit_job_id)
        if busy is not None:
            raise _busy_409(registered, busy, now.last_audit_job_id)
        raise HTTPException(
            status_code=409,
            detail=f"the audit state of {registered} changed while this audit was starting; send it again",
        )
    executor.submit_job(
        job_id,
        repo=registered,
        title=task.splitlines()[0],
        body=task,
        kind="standard_audit",
    )
    await _audit(
        request,
        "standard.audit_started",
        {"owner": owner, "kind": kind, "repo": registered, "version": standard.version, "job_id": job_id},
    )
    return {
        "job_id": job_id,
        "owner": owner,
        "kind": kind,
        "repo": registered,
        "version": standard.version,
        "template": template,
    }
