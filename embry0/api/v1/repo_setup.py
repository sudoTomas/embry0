"""Setting up testing with the onboarding agent: start, review, apply, turn down (ADR 0019).

GET .../setup/cost is what a person reads before starting: recorded costs
of past setup runs, the per-job budget, the model and the paying account.
POST .../setup/propose starts the onboarding agent in propose mode: it
drafts a test setup, proves the app starts with it, and stores the draft as
a setup proposal instead of the test setup. GET .../setup/proposal reads the
latest proposal with the free check of it. Apply writes it as the test setup
(recorded, so History can undo it); reject turns it down. POST
.../setup/skip-testing stores a starter with testing off. GET .../readiness
is the checklist the repo page opens on, built from stored facts only.

Operator tier for every write, the same as saving a test setup: the router
is mounted with ``auth_deps``, which refuses a read-scope key on POST. Every
write also checks the calling key's repository allowlist before it reads or
changes anything.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from embry0.api.deps import request_actor
from embry0.api.repo_allowlist import enforce_repo_allowlist
from embry0.api.trigger_context import resolve_trigger_labels
from embry0.api.v1.jobs import CREATE_A_JOB, start_onboard_job
from embry0.audit.actions import AuditAction
from embry0.audit.helpers import emit_audit
from embry0.services.repo_readiness import build_readiness, readiness_inputs
from embry0.services.repo_setup import (
    ALREADY_RUNNING,
    Refused,
    apply_proposal,
    cost_facts,
    medium_model,
    proposal_view,
    reject_proposal,
    settle,
    skip_testing,
    start_branch,
)
from embry0.storage.repositories.repo_setup_proposals import LiveProposalExists

router = APIRouter()


class ProposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_spend: bool = False


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledged: list[str] = Field(default_factory=list)


async def _audit(request: Request, action: AuditAction, details: dict[str, Any]) -> None:
    config = getattr(request.app.state, "config", None)
    await emit_audit(
        getattr(request.app.state, "db", None),
        action,
        actor=request_actor(request),
        details=details,
        audit_log_path=getattr(config, "audit_log_path", None),
    )


@router.get("/repos/{full_repo:path}/setup/cost")
async def get_setup_cost(full_repo: str, request: Request) -> dict[str, Any]:
    return await cost_facts(request.app.state, full_repo)


@router.post("/repos/{full_repo:path}/setup/propose")
async def propose_setup(full_repo: str, request: Request, req: ProposeRequest | None = None) -> dict[str, str]:
    if req is None or not req.confirm_spend:
        raise HTTPException(status_code=422, detail="Confirm the cost first: send confirm_spend true.")
    # Before anything changes: a key that may not start work here must not
    # retire the proposal waiting here either.
    await enforce_repo_allowlist(request, full_repo, action=CREATE_A_JOB)
    state = request.app.state
    proposals = state.repo_setup_proposals_repo
    latest = await proposals.latest(full_repo)
    if latest is not None:
        await settle(state, latest)  # a run a restart cut off no longer holds the repository
    await proposals.mark_stale_pending(full_repo)
    current = await state.qa_configs_repo.get(full_repo)
    try:
        proposal_id = await proposals.create(
            full_repo,
            base_updated_at=current["updated_at"] if current is not None else None,
            created_by=request_actor(request),
        )
    except LiveProposalExists:
        raise HTTPException(status_code=409, detail=ALREADY_RUNNING) from None
    try:
        job_id = await start_onboard_job(
            request,
            repo=full_repo,
            branch=await start_branch(state, full_repo),
            agent_models={"onboarding": await medium_model(state)},
            proposal_id=proposal_id,
        )
    except Exception:
        await proposals.delete(proposal_id)
        raise
    await proposals.attach_job(proposal_id, job_id)
    return {"proposal_id": proposal_id, "job_id": job_id}


@router.get("/repos/{full_repo:path}/setup/proposal")
async def get_setup_proposal(full_repo: str, request: Request) -> dict[str, Any]:
    view = await proposal_view(request.app.state, full_repo)
    if view is None:
        raise HTTPException(status_code=404, detail="embry0 has not proposed a test setup for this repository.")
    return view


@router.post("/repos/{full_repo:path}/setup/proposal/{proposal_id}/apply")
async def apply_setup_proposal(
    full_repo: str, proposal_id: str, request: Request, req: ApplyRequest | None = None
) -> dict[str, int | None]:
    await enforce_repo_allowlist(request, full_repo, action="apply a setup proposal")
    try:
        revision_id = await apply_proposal(
            request.app.state,
            full_repo,
            proposal_id,
            acknowledged=req.acknowledged if req is not None else [],
            actor=request_actor(request),
        )
    except Refused as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    await _audit(
        request, "repo.setup_applied", {"repo": full_repo, "proposal_id": proposal_id, "revision_id": revision_id}
    )
    return {"revision_id": revision_id}


@router.post("/repos/{full_repo:path}/setup/proposal/{proposal_id}/reject")
async def reject_setup_proposal(full_repo: str, proposal_id: str, request: Request) -> dict[str, str]:
    await enforce_repo_allowlist(request, full_repo, action="turn down a setup proposal")
    try:
        await reject_proposal(request.app.state, full_repo, proposal_id, actor=request_actor(request))
    except Refused as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    await _audit(request, "repo.setup_rejected", {"repo": full_repo, "proposal_id": proposal_id})
    return {"proposal_id": proposal_id, "status": "rejected"}


@router.post("/repos/{full_repo:path}/setup/skip-testing")
async def skip_setup_testing(full_repo: str, request: Request) -> dict[str, int | None]:
    await enforce_repo_allowlist(request, full_repo, action="skip testing")
    try:
        revision_id = await skip_testing(request.app.state, full_repo, actor=request_actor(request))
    except Refused as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return {"revision_id": revision_id}


@router.get("/repos/{full_repo:path}/readiness")
async def get_readiness(full_repo: str, request: Request) -> dict[str, Any]:
    """The checklist the repo page opens on (``build_readiness``). It reads
    stored facts only: no model call and no forge call."""
    registered = await request.app.state.repos_repo.get(full_repo)
    if registered is None:
        raise HTTPException(status_code=404, detail="repo not registered")
    labels = await resolve_trigger_labels(request)
    inputs = await readiness_inputs(
        request.app.state, full_repo, registered, trigger_label=labels[0] if labels else "embry0"
    )
    return build_readiness(inputs)


@router.get("/setup/proposals")
async def list_setup_proposals(
    request: Request, status: Literal["pending"] = Query("pending")
) -> dict[str, list[dict[str, Any]]]:
    """Every proposal waiting for a person, newest first, for the Overview.
    ``pending`` is the one status listed; any other is a 422."""
    pending = await request.app.state.repo_setup_proposals_repo.list_pending()
    return {
        "proposals": [{"id": p["id"], "repo": p["repo"], "created_at": p["created_at"].isoformat()} for p in pending]
    }
