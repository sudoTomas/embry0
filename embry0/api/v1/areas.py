"""A repository's areas (spec 2026-10-25, 2026-11-10): the document embry0 keeps, and mapping them with an agent.

``GET /repos/{repo}/-/areas`` reads the stored document; ``PUT`` replaces it
(checked, recorded, undoable through the repository's history). ``POST
.../areas/map`` starts the agent with the model and key a person chose (run
with); its document waits on a proposal (``GET .../areas/proposal``) for a
person to apply or reject. The repository carries no file for its areas.

Writes (``PUT``, map, apply, reject) take an operate key, and a key whose
repository allowlist covers the repository: each is refused before anything
is read, so the answer is the same whether or not the repository is
registered. A read by a key whose list does not cover the repository answers
as for a repository nobody registered (:mod:`embry0.api.repository_reads`).
"""

from __future__ import annotations

from typing import Any, Literal

import structlog
from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from embry0.api.actor_visibility import actor_shown, with_actors
from embry0.api.budget import unattended_cap
from embry0.api.deps import request_actor
from embry0.api.document_requests import if_match_versions, read_capped_body
from embry0.api.repo_allowlist import covered_slugs, enforce_repo_allowlist, key_covers
from embry0.api.repository_reads import covered
from embry0.api.schemas import RunWithRequest
from embry0.api.v1.jobs import CREATE_A_JOB
from embry0.api.v1.run_with import checked_run_with
from embry0.audit.actions import AuditAction
from embry0.audit.helpers import emit_audit
from embry0.services.area_map import map_task, start_map_areas_job
from embry0.services.areas_document import MAX_TEXT_BYTES, TOO_LARGE, DocumentInvalid, DocumentTooLarge, parse_body
from embry0.services.areas_proposals import (
    ALREADY_RUNNING,
    Refused,
    apply_proposal,
    proposal_view,
    reject_proposal,
    settle,
)
from embry0.services.run_with import RunWith
from embry0.storage.repositories.repo_areas import AreasChanged
from embry0.storage.repositories.repo_areas_proposals import LiveAreasProposalExists
from embry0.workflows.issue_to_pr.areas_report import TEMPLATE

logger = structlog.get_logger(__name__)
router = APIRouter()

#: What changing a repository's areas is called in a refusal and the log.
CHANGE_AREAS = "change a repository's areas"
#: What a stale If-Match is told.
CHANGED_MEANWHILE = "These areas changed since you read them. Read them again and redo your change."
#: What a write without If-Match is told when another first write stored the repository's areas at the same time.
WRITTEN_MEANWHILE = (
    "Someone stored this repository's areas while your change was being saved. Read them again and redo your change."
)


def _etag(generation: int) -> dict[str, str]:
    """The ``ETag`` a read or a write answers with: the generation, which ``If-Match`` takes back."""
    return {"ETag": f'"{generation}"'}


class MapBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The model and key the job runs with; none is the usual run.
    run_with: RunWithRequest | None = None


async def _registered(request: Request, repo: str, *, reads: bool = True) -> str:
    """The registry's spelling of ``repo``; 404 when it is not registered, or when the caller may not ``reads`` it."""
    registry = getattr(request.app.state, "repos_repo", None)
    registered = await registry.registered_as(repo) if registry is not None and reads else None
    if registered is None:
        raise HTTPException(status_code=404, detail=f"{repo} is not a registered repository")
    return str(registered)


async def _audit(request: Request, action: AuditAction, details: dict[str, Any]) -> None:
    config = getattr(request.app.state, "config", None)
    await emit_audit(
        getattr(request.app.state, "db", None),
        action,
        actor=request_actor(request),
        details=details,
        audit_log_path=getattr(config, "audit_log_path", None),
    )


async def _newest_job(request: Request, repo: str) -> dict[str, Any] | None:
    jobs = getattr(request.app.state, "jobs_repo", None)
    newest = (await jobs.newest_per_repo([repo], pipeline_template=TEMPLATE)).get(repo) if jobs is not None else None
    if not newest:
        return None
    return {k: newest.get(k) for k in ("job_id", "status", "created_at", "finished_at", "pr_url", "live")}


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


async def _imported(request: Request, repo: str) -> dict[str, Any] | None:
    """How the one-time import of the repository's old file ended, or None when it was not imported."""
    ledger = getattr(request.app.state, "repo_areas_imports_repo", None)
    row = await ledger.get(repo) if ledger is not None else None
    if row is None or row["outcome"] != "imported":
        return None
    return {"outcome": row["outcome"], "at": _iso(row["updated_at"]), "blob_sha": row["blob_sha"]}


async def _newest_proposal(request: Request, repo: str) -> dict[str, Any] | None:
    proposals = request.app.state.repo_areas_proposals_repo
    latest = await proposals.latest(repo)
    if latest is None:
        return None
    latest, _ = await settle(request.app.state, latest)
    return {
        "id": latest["id"],
        "status": latest["status"],
        "job_id": latest["job_id"],
        "at": _iso(latest["created_at"]),
    }


@router.get("/repos/{full_repo:repo}/-/areas")
async def get_areas(
    full_repo: str, request: Request, format: Literal["json", "yaml"] = Query(default="json")
) -> Response:
    """The stored document and its values, the newest map and its proposal. A repository the caller's key does
    not cover is not registered for it (embry0.api.repository_reads)."""
    repo = await _registered(request, full_repo, reads=key_covers(request, full_repo))
    stored = await request.app.state.areas.get(repo)
    etag = _etag(stored.generation)
    if format == "yaml":
        return Response(content=stored.yaml_text, media_type="application/yaml", headers=etag)
    shown = await actor_shown(request)
    body = {
        "repo": repo,
        "yaml_text": stored.yaml_text,
        "areas": [
            {"name": a.name, "slug": a.slug, "paths": list(a.paths), "logs": list(a.logs), "owners": list(a.owners)}
            for a in stored.areas
        ],
        "generation": stored.generation,
        "updated_by": shown(stored.updated_by or None),
        "updated_at": _iso(stored.updated_at),
        "imported": await _imported(request, repo),
        "latest_job": await _newest_job(request, repo),
        "proposal": await _newest_proposal(request, repo),
    }
    return JSONResponse(jsonable_encoder(body), headers=etag)


@router.put("/repos/{full_repo:repo}/-/areas")
async def put_areas(
    full_repo: str,
    request: Request,
    dry_run: bool = Query(default=False),
    reason: str = Query(default="", max_length=500),
    source: Literal["person", "file"] = Query(default="person"),
    if_match: str | None = Header(default=None),
) -> JSONResponse:
    """Replace the repository's areas with the body (YAML, or a JSON object), every problem in one 422.

    ``If-Match`` (the ``ETag`` a GET or a PUT returned) refuses with 409 a
    document someone changed since; ``dry_run`` checks and compares, storing
    nothing. A text equal to the stored one records nothing (``revision_id``
    null). The answer's ``ETag`` is the generation stored after it.
    """
    await enforce_repo_allowlist(request, full_repo, action=CHANGE_AREAS)
    repo = await _registered(request, full_repo)
    expected = if_match_versions(if_match)
    body = await read_capped_body(request, MAX_TEXT_BYTES, TOO_LARGE)
    try:
        text, values = parse_body(body, request.headers.get("content-type", ""))
        result = await request.app.state.areas.save(
            repo,
            text,
            values,
            expected=expected,
            actor=request_actor(request),
            source=source,
            reason=reason,
            dry_run=dry_run,
        )
    except DocumentTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from None
    except DocumentInvalid as exc:
        raise HTTPException(status_code=422, detail=[p.to_dict() for p in exc.problems]) from None
    except AreasChanged:
        raise HTTPException(
            status_code=409, detail=CHANGED_MEANWHILE if expected is not None else WRITTEN_MEANWHILE
        ) from None
    if result.revision_id is not None:
        await _audit(
            request,
            "repo.areas_set",
            {"repo": repo, "revision_id": result.revision_id, "areas": len(result.changes), "source": source},
        )
    answer: dict[str, Any] = {
        "repo": repo,
        "generation": result.generation,
        "revision_id": result.revision_id,
        "changes": result.changes,
    }
    if dry_run:
        answer.update(would_change=result.would_change, text_diff=result.text_diff)
    return JSONResponse(jsonable_encoder(answer), headers=_etag(result.generation))


@router.post("/repos/{full_repo:repo}/-/areas/map", status_code=202)
async def map_areas(full_repo: str, request: Request, body: MapBody | None = None) -> dict[str, Any]:
    """Start an agent job that writes the repository's areas document for a person to apply.

    Refused with 409 while an earlier map of the repository is running, 429
    while the budget gate refuses, and 422 for a model or key that cannot
    run. A map waiting for a person is retired (``stale``) by a new one. A
    key whose allowlist does not cover the repository is refused before
    anything is read, so the answer is the same whether or not it is
    registered.
    """
    await enforce_repo_allowlist(request, full_repo, action=CREATE_A_JOB)
    repo = await _registered(request, full_repo)
    run_with = await checked_run_with(request, body.run_with if body else None, repo)
    return await start_map(request, repo, run_with)


async def start_map(request: Request, repo: str, run_with: RunWith | None) -> dict[str, Any]:
    """Start a map of ``repo``'s areas (the registry's spelling) with ``run_with`` checked: the one path Map and a
    map job's Run again take. Retires the map waiting for a person and puts it back if the job does not start; the
    job is named on its proposal before it is submitted. ``{job_id, repo, proposal_id}``; 409 while a map runs,
    429 while the budget gate refuses."""
    state = request.app.state
    proposals = state.repo_areas_proposals_repo
    latest = await proposals.latest(repo)
    if latest is not None:
        latest, _ = await settle(state, latest)  # a run a restart cut off no longer holds the repository
    newest = await _newest_job(request, repo)
    starting = latest is not None and latest["status"] == "running"
    if starting or (newest and newest.get("live")):
        # Name the job that runs: the running map's own, else the newest job when it is live, never a finished one.
        job_id = (latest or {}).get("job_id") if starting else None
        job_id = job_id or ((newest or {}).get("job_id") if newest and newest.get("live") else None)
        running = f"is already running ({job_id})" if job_id else "is already starting"
        raise HTTPException(status_code=409, detail=f"a map of {repo}'s areas {running}")
    executor = getattr(state, "issue_executor", None)
    actor = request_actor(request)
    refusal = await executor.dispatch_refusal(actor=actor) if executor is not None else None
    if refusal is not None:
        raise HTTPException(status_code=429, detail=refusal.public_message)
    stacks = getattr(state, "watcher_stacks_repo", None)
    stack = await stacks.get(repo) if stacks is not None else None
    budget_usd = await unattended_cap(request, repo, "area_map_job_usd")
    stored = await state.areas.get(repo)
    # The map waiting for a person is retired by this one, and put back if this one's job does not start.
    try:
        proposal_id, retired = await proposals.start(repo, base_generation=stored.generation, created_by=actor)
    except LiveAreasProposalExists:
        raise HTTPException(status_code=409, detail=ALREADY_RUNNING) from None
    task = map_task(repo, existing=stored.yaml_text, logql=getattr(stack, "logql", None) if stack is not None else None)

    async def attach(job_id: str) -> None:
        await proposals.attach_job(proposal_id, job_id)

    try:
        job_id = await start_map_areas_job(
            repo,
            jobs=state.jobs_repo,
            executor=executor,
            templates=getattr(state, "templates_repo", None),
            task=task,
            budget_usd=budget_usd,
            dispatched_by=actor,
            proposal_id=proposal_id,
            attach=attach,
            run_with=run_with,
            db=getattr(state, "db", None),
            audit_log_path=getattr(state.config, "audit_log_path", None),
        )
    except (LookupError, RuntimeError) as exc:
        await proposals.withdraw(proposal_id, revive=retired)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        await proposals.withdraw(proposal_id, revive=retired)
        raise
    return {"job_id": job_id, "repo": repo, "proposal_id": proposal_id}


@router.get("/repos/{full_repo:repo}/-/areas/proposal")
async def get_areas_proposal(full_repo: str, request: Request) -> dict[str, Any]:
    """The newest map of the repository's areas, as its card reads it. A repository the caller's key does not
    cover has no map, nor the job that made one (embry0.api.repository_reads)."""
    registry = getattr(request.app.state, "repos_repo", None)
    repo = await registry.registered_as(full_repo) if registry is not None and key_covers(request, full_repo) else None
    view = await proposal_view(request.app.state, str(repo)) if repo is not None else None
    if view is None:
        raise HTTPException(status_code=404, detail="embry0 has not mapped this repository's areas.")
    return with_actors(view, await actor_shown(request), "created_by", "decided_by")


@router.post("/repos/{full_repo:repo}/-/areas/proposal/{proposal_id}/apply")
async def apply_areas_proposal(full_repo: str, proposal_id: str, request: Request) -> dict[str, int | None]:
    """Store a waiting map as the repository's areas: a revision History can undo."""
    await enforce_repo_allowlist(request, full_repo, action="apply a map of a repository's areas")
    repo = await _registered(request, full_repo)
    try:
        revision_id = await apply_proposal(request.app.state, repo, proposal_id, actor=request_actor(request))
    except Refused as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    await _audit(
        request, "repo.areas_map_applied", {"repo": repo, "proposal_id": proposal_id, "revision_id": revision_id}
    )
    return {"revision_id": revision_id}


@router.post("/repos/{full_repo:repo}/-/areas/proposal/{proposal_id}/reject")
async def reject_areas_proposal(full_repo: str, proposal_id: str, request: Request) -> dict[str, str]:
    """Turn a waiting map down; the stored areas stay as they are."""
    await enforce_repo_allowlist(request, full_repo, action="reject a map of a repository's areas")
    repo = await _registered(request, full_repo)
    try:
        await reject_proposal(request.app.state, repo, proposal_id, actor=request_actor(request))
    except Refused as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    await _audit(request, "repo.areas_map_rejected", {"repo": repo, "proposal_id": proposal_id})
    return {"proposal_id": proposal_id, "status": "rejected"}


@router.get("/areas/proposals")
async def list_areas_proposals(
    request: Request, status: Literal["pending"] = Query("pending")
) -> dict[str, list[dict[str, Any]]]:
    """Every map waiting for a person, newest first, for the dashboard's brief bar. ``pending`` is the one
    status listed; any other is a 422. To a key with an allowlist, those of the repositories it covers."""
    proposals = request.app.state.repo_areas_proposals_repo
    pending = covered(request, await proposals.list_pending(repos=covered_slugs(request)))
    return {
        "proposals": [{"id": p["id"], "repo": p["repo"], "created_at": p["created_at"].isoformat()} for p in pending]
    }
