"""Organization needs API (ADR 0011): what embry0 still needs to maintain an owner.

A discovery run syncs its owner's needs when it finishes; a read answers
from what is stored and writes nothing, except a one-time catch-up for a
run that finished unsynced. Dismiss and reopen are a person's answer and
are kept across runs.
"""

from __future__ import annotations

from typing import Any, cast

import structlog
from fastapi import APIRouter, HTTPException, Request

from embry0.api.deps import request_actor, require_admin
from embry0.audit.helpers import emit_audit
from embry0.services.org_needs import OrgNeedsService
from embry0.storage.repositories.organizations import OrganizationsRepository

logger = structlog.get_logger(__name__)
router = APIRouter()


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
    """
    owner = await _owner(request, owner)
    service = cast("OrgNeedsService", needs_service(request))
    return await service.read(owner)


@router.post("/orgs/{owner}/needs/{need_id}/dismiss", dependencies=[require_admin])
async def dismiss_need(owner: str, need_id: str, request: Request) -> dict[str, Any]:
    """Dismiss a need: its status becomes dismissed and its case is closed.
    Rediscovery keeps the answer for the same kind and repositories."""
    owner = await _owner(request, owner)
    service = cast("OrgNeedsService", needs_service(request))
    need = await service.dismiss(owner, need_id)
    if need is None:
        raise HTTPException(status_code=404, detail=f"no need {need_id} for {owner}")
    await emit_audit(
        getattr(request.app.state, "db", None),
        "organization.need_dismiss",
        actor=request_actor(request),
        details={"owner": owner, "need_id": need_id, "kind": need["kind"]},
    )
    return need


@router.post("/orgs/{owner}/needs/{need_id}/reopen", dependencies=[require_admin])
async def reopen_need(owner: str, need_id: str, request: Request) -> dict[str, Any]:
    """Reopen a dismissed or done need: its status becomes open and a case
    awaiting decision is filed for it again."""
    owner = await _owner(request, owner)
    service = cast("OrgNeedsService", needs_service(request))
    need = await service.reopen(owner, need_id)
    if need is None:
        raise HTTPException(status_code=404, detail=f"no need {need_id} for {owner}")
    await emit_audit(
        getattr(request.app.state, "db", None),
        "organization.need_reopen",
        actor=request_actor(request),
        details={"owner": owner, "need_id": need_id, "kind": need["kind"]},
    )
    return need
