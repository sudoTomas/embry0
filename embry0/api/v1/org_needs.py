"""Organization needs API (ADR 0011): what embry0 still needs to maintain an owner.

A discovery run syncs its owner's needs when it finishes; a read answers
from what is stored and writes nothing, except a one-time catch-up for a
run that finished unsynced. Dismiss and reopen are a person's answer and
are kept across runs.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import structlog
from fastapi import APIRouter, HTTPException, Request

from embry0.api.case_allowlist import without_hidden_cases
from embry0.api.deps import request_actor, require_admin
from embry0.api.repo_allowlist import answer_as_missing, covered_slugs, enforce_owner_allowlist, key_covers
from embry0.api.repository_reads import owner_hidden
from embry0.api.served import ServedRoute
from embry0.audit.helpers import emit_audit
from embry0.services.org_needs import OrgNeedsService, narrow_need
from embry0.storage.repositories.organizations import OrganizationsRepository

logger = structlog.get_logger(__name__)
router = APIRouter(route_class=ServedRoute)


def needs_service(request: Request, *, required: bool = True) -> OrgNeedsService | None:
    """The needs service over this app's storage, or None when the storage is
    not initialized and ``required`` is off (503 when it is on)."""
    st = request.app.state
    needs_repo = getattr(st, "org_needs_repo", None)
    facts_repo = getattr(st, "org_repos_repo", None)
    if needs_repo is None or facts_repo is None:
        if required:
            raise HTTPException(status_code=503, detail="organization storage not initialized")
        return None
    return OrgNeedsService(needs_repo, facts_repo, getattr(st, "healing_cases", None))


async def _owner(request: Request, owner: str) -> str:
    repository = getattr(request.app.state, "organizations_repo", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="organization storage not initialized")
    org = await cast("OrganizationsRepository", repository).get(owner)
    if org is None:
        raise HTTPException(status_code=404, detail=f"no organization {owner}")
    return org.owner


@router.get("/orgs/{owner}/needs")
async def organization_needs(owner: str, request: Request) -> dict[str, Any]:
    """The needs of an owner, as its newest finished discovery run left them.

    Each need names its kind, the repositories it covers, the action that
    answers it and its status: open, done (the run no longer finds it) or
    dismissed (a person said no, here or by closing its case; kept across
    runs). Counts are per status. ``run_id`` is the run the needs were
    synced from, or null before the first one. Read-only, except once for
    a run that finished before its needs were synced.

    An owner the caller's key does not cover as a whole is not registered
    for it (embry0.api.repository_reads), and a need names no case the key
    does not read (embry0.api.case_allowlist): one filed on the owner.
    """
    if await owner_hidden(request, owner):
        raise HTTPException(status_code=404, detail=f"no organization {owner}")
    owner = await _owner(request, owner)
    service = cast("OrgNeedsService", needs_service(request))
    listing = await service.read(owner, keep=_keep(request))
    await without_hidden_cases(request, listing.get("needs") or [])
    return listing


def _keep(request: Request) -> Callable[[str], bool] | None:
    """For a key with an allowlist, the repositories a need may name to it: those its list covers. A need is narrowed
    to them, its words written again, and one naming none of them is not there for it (``narrow_need``): discovery
    reads every repository under the owner, those the list leaves out too. None for every other caller."""
    if covered_slugs(request) is None:
        return None
    return lambda repo: key_covers(request, repo)


async def _hide_unless_shown(request: Request, service: OrgNeedsService, owner: str, need_id: str, action: str) -> None:
    """Answer a need the caller does not read (:func:`_keep`) as one that does not exist, before acting on it: the
    refusal logged and audited, as an action on another repository's item is."""
    keep = _keep(request)
    if keep is None:
        return
    need = await service.get(owner, need_id)
    if need is not None and narrow_need(need, keep) is None:
        await answer_as_missing(
            request,
            f"need {need_id}",
            HTTPException(status_code=404, detail=f"no need {need_id} for {owner}"),
            action=action,
        )


async def _shown(request: Request, need: dict[str, Any]) -> dict[str, Any]:
    """``need`` as the caller reads it: narrowed to its repositories (:func:`_keep`), naming no case it does not
    read."""
    keep = _keep(request)
    shown = need if keep is None else (narrow_need(need, keep) or need)
    await without_hidden_cases(request, [shown])
    return shown


@router.post("/orgs/{owner}/needs/{need_id}/dismiss", dependencies=[require_admin])
async def dismiss_need(owner: str, need_id: str, request: Request) -> dict[str, Any]:
    """Dismiss a need: its status becomes dismissed and its case is closed.
    Rediscovery keeps the answer for the same kind and repositories."""
    await enforce_owner_allowlist(request, owner, action="dismiss an organization need")
    owner = await _owner(request, owner)
    service = cast("OrgNeedsService", needs_service(request))
    await _hide_unless_shown(request, service, owner, need_id, "dismiss an organization need")
    need = await service.dismiss(owner, need_id)
    if need is None:
        raise HTTPException(status_code=404, detail=f"no need {need_id} for {owner}")
    await emit_audit(
        getattr(request.app.state, "db", None),
        "organization.need_dismiss",
        actor=request_actor(request),
        details={"owner": owner, "need_id": need_id, "kind": need["kind"]},
    )
    return await _shown(request, need)


@router.post("/orgs/{owner}/needs/{need_id}/reopen", dependencies=[require_admin])
async def reopen_need(owner: str, need_id: str, request: Request) -> dict[str, Any]:
    """Reopen a dismissed or done need: its status becomes open and a case
    awaiting decision is filed for it again."""
    await enforce_owner_allowlist(request, owner, action="reopen an organization need")
    owner = await _owner(request, owner)
    service = cast("OrgNeedsService", needs_service(request))
    await _hide_unless_shown(request, service, owner, need_id, "reopen an organization need")
    need = await service.reopen(owner, need_id)
    if need is None:
        raise HTTPException(status_code=404, detail=f"no need {need_id} for {owner}")
    await emit_audit(
        getattr(request.app.state, "db", None),
        "organization.need_reopen",
        actor=request_actor(request),
        details={"owner": owner, "need_id": need_id, "kind": need["kind"]},
    )
    return await _shown(request, need)
