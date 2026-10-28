"""User management endpoints (RAV-1056 Phase A) — admin-tier only.

Users are JIT-mirrored from SSO logins; roles stay Auth0-owned. These
endpoints cover the embry0-side controls: listing, disable/enable, and
force-logout (session-epoch bump). Mounted with ``dependencies=[require_admin]``.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from embry0.api.deps import bust_user_gate_cache, request_actor
from embry0.api.v1.handoffs import LoginBody, set_login
from embry0.audit.helpers import emit_audit

router = APIRouter()


def _serialize(row: Any) -> dict[str, Any]:
    d = asdict(row)
    for k in ("first_login_at", "last_login_at", "created_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


class DisabledBody(BaseModel):
    disabled: bool


@router.get("/users")
async def list_users(request: Request, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    repo = request.app.state.users_repo
    users = await repo.list_users(limit=min(limit, 500), offset=max(offset, 0))
    total = await repo.count()
    return {"users": [_serialize(u) for u in users], "total": total}


@router.put("/users/{user_id}/disabled")
async def set_disabled(user_id: str, body: DisabledBody, request: Request) -> dict[str, Any]:
    repo = request.app.state.users_repo
    row = await repo.set_disabled(user_id, body.disabled)
    if row is None:
        raise HTTPException(status_code=404, detail=f"user {user_id!r} not found")
    bust_user_gate_cache(row.auth0_sub)
    config = request.app.state.config
    await emit_audit(
        getattr(request.app.state, "db", None),
        "user.disabled" if body.disabled else "user.enabled",
        actor=request_actor(request),
        details={"user_id": row.id, "email": row.email},
        audit_log_path=config.audit_log_path,
    )
    return _serialize(row)


@router.post("/users/{user_id}/force-logout")
async def force_logout(user_id: str, request: Request) -> dict[str, Any]:
    """Invalidate every session cookie the user currently holds."""
    repo = request.app.state.users_repo
    row = await repo.bump_epoch(user_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"user {user_id!r} not found")
    bust_user_gate_cache(row.auth0_sub)
    config = request.app.state.config
    await emit_audit(
        getattr(request.app.state, "db", None),
        "user.force_logout",
        actor=request_actor(request),
        details={"user_id": row.id, "email": row.email, "new_epoch": row.session_epoch},
        audit_log_path=config.audit_log_path,
    )
    return _serialize(row)


@router.put("/users/{user_id}/github-login")
async def set_github_login(user_id: str, body: LoginBody, request: Request) -> dict[str, Any]:
    """Name another person's GitHub login (hand-offs, spec 2026-10-27): the quests that login owns become theirs to decide."""
    return await set_login(request, user_id, body.login)
