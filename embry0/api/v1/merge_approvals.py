"""Merge approvals (#319 phase 2): read the asks, and relay a human's decision.

``POST /merge-approvals/{id}/decide`` is what the chat relay calls (through
the ``merge_decide`` MCP tool) when someone in the operator's approval chat
answers "merge it" or "don't", and what the dashboard calls with ``decision``
alone when an admin answers there (spec 3.8). Every check that decides
whether a merge may happen lives in
:class:`embry0.services.merge_approvals.MergeApprovalService`; this module
only translates.

A ``merge``-scope key — the one the relay holds — may call ``decide`` but no
other mutation; that restriction is enforced in ``embry0.api.deps``. The
relay's form in turn is accepted only from that key, the master key or an
admin: it names the person who answered, so anyone else who could send it
would be attributing a decision to a name of their choosing.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from embry0.api.deps import TIER_MERGE, ensure_admin, request_actor
from embry0.api.repo_allowlist import enforce_repo_allowlist, restricted_key_name
from embry0.audit.helpers import emit_audit
from embry0.services.merge_approvals import DASHBOARD_CHANNEL, DecisionRefused, MergeApprovalService

router = APIRouter()

#: Route template a merge-scope key may POST to (see deps.MERGE_SCOPE_MUTATIONS).
DECIDE_PATH = "/merge-approvals/{approval_id}/decide"


class DecideBody(BaseModel):
    """Two forms on one route. The relay's carries ``decided_by`` and ``chat_id``
    (who answered, in which chat); the dashboard's carries ``decision`` alone,
    and the route names the actor from who authenticated (spec 3.8)."""

    decision: str = Field(min_length=1, max_length=20, description="'approve' or 'reject'")
    decided_by: str | None = Field(
        default=None, min_length=1, max_length=200, description="who answered in the chat (the relay's form)"
    )
    chat_id: str | None = Field(
        default=None, min_length=1, max_length=300, description="the chat the answer came from (the relay's form)"
    )


RELAY_FORM_REFUSED = 'the relay\'s form is for the merge key; decide from the dashboard with {"decision"} instead'


async def _ensure_relay(request: Request) -> None:
    """The relay's form is for the merge-scope key, the master key or an admin.

    The merge key is read off the principal the router's auth resolved for
    this request; every other caller gets the admin gate's answer, with a
    detail that points at the dashboard's form instead. Under AUTH_DEV_MODE
    the admin gate admits, as every gate does.
    """
    principal = getattr(request.state, "principal", None)
    if principal is not None and principal.tier == TIER_MERGE:
        return
    try:
        await ensure_admin(request)
    except HTTPException as refused:
        if refused.status_code != 403:
            raise
        raise HTTPException(status_code=403, detail=RELAY_FORM_REFUSED) from None


def _service(request: Request) -> MergeApprovalService:
    # Duck-typed on purpose: an isinstance gate here 503s on any stand-in,
    # which is how the watcher-stacks route failed its own tests (#391).
    service = getattr(request.app.state, "merge_approvals", None)
    if service is None:
        raise HTTPException(status_code=503, detail="merge approvals are not available on this deployment")
    return cast("MergeApprovalService", service)


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}


@router.get("/merge-approvals")
async def list_merge_approvals(
    request: Request,
    status: str | None = Query(
        default=None, pattern="^(pending|approved|rejected|merged|failed|stale|closed|expired)$"
    ),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    service = _service(request)
    rows = await service.list_recent(status=status, limit=limit)
    return {"items": [_serialize(r) for r in rows], "mode": service.mode}


@router.get("/merge-approvals/{approval_id}")
async def get_merge_approval(approval_id: str, request: Request) -> dict[str, Any]:
    row = await _service(request).get(approval_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no merge approval {approval_id!r}")
    return _serialize(row)


@router.post(DECIDE_PATH)
async def decide_merge_approval(approval_id: str, body: DecideBody, request: Request) -> dict[str, Any]:
    service = _service(request)
    if (body.decided_by is None) != (body.chat_id is None):
        raise HTTPException(
            status_code=422,
            detail="decided_by and chat_id come together: both for the relay's form, neither for the dashboard's",
        )
    via: Literal["chat", "dashboard"]
    decided_by: str
    chat_id: str
    if body.decided_by is None or body.chat_id is None:
        # The dashboard's form: an admin, named by the route, with no chat in the loop.
        await ensure_admin(request)
        via, decided_by, chat_id = "dashboard", request_actor(request), DASHBOARD_CHANNEL
    else:
        # The relay's form: the relay's key, or an admin standing in for it.
        await _ensure_relay(request)
        via, decided_by, chat_id = "chat", body.decided_by, body.chat_id
    if restricted_key_name(request) is not None:
        # Only a restricted key pays for the extra read; an unknown id falls
        # through to decide(), which owns the 404.
        row = await service.get(approval_id)
        if row is not None:
            await enforce_repo_allowlist(request, row.get("repo"), action="decide a merge")
    try:
        result = await service.decide(
            approval_id, decision=body.decision, decided_by=decided_by, chat_id=chat_id, via=via
        )
    except DecisionRefused as refused:
        raise HTTPException(status_code=refused.status_code, detail=refused.detail) from None

    config = request.app.state.config
    await emit_audit(
        getattr(request.app.state, "db", None),
        "merge.decided",
        # Relay's form: the caller is the relay (its merge-scope key), the person
        # is decided_by. Dashboard's form: both are who authenticated.
        actor=request_actor(request),
        details={
            "approval_id": approval_id,
            "decision": body.decision,
            "decided_by": decided_by,
            "via": via,
            "outcome": result["outcome"],
            "status": result["status"],
            "dry_run": result["dry_run"],
            "pr_url": result["pr_url"],
            "head_sha": result["head_sha"],
        },
        audit_log_path=getattr(config, "audit_log_path", None),
    )
    return result
