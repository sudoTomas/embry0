"""Self-service API key management (RAV-1056 Phase B).

Mounted behind the normal operator+ auth; per-route logic enforces
ownership and scope caps:

- session users mint keys bound to their users-row, scope capped at their
  own tier (operator -> read/operate; admin -> +admin);
- admin principals (admin session, master key, admin-scope key) may mint
  unowned SERVICE keys and see/revoke every key;
- operators see and revoke only their own keys.

A key may be limited to a repository allowlist (``allowed_repos``), set at
mint or later by the key's owner or an admin; ``embry0.api.repo_allowlist``
enforces it. A key carrying an allowlist may not mint or change keys: an
unrestricted key it minted would undo its own restriction.

The plaintext token appears exactly once, in the mint response.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from embry0.api.deps import bust_api_key_cache, request_actor, unidentified_caller_tier
from embry0.api.repo_allowlist import answer_as_missing, restricted_key_name, wider_than_allowlist
from embry0.api.schemas.repo_path import validate_repo_path
from embry0.api.session import TIER_ADMIN
from embry0.audit.helpers import emit_audit

router = APIRouter()

_SCOPE_RANK = {"read": 0, "operate": 1, "merge": 1, "propose": 1, "admin": 2}
_TIER_MAX_SCOPE = {"operator": "operate", "admin": "admin"}
MAX_ALLOWED_REPOS = 100


def _clean_allowed_repos(value: list[str] | None) -> list[str] | None:
    """Validated, de-duplicated (case-insensitively, first spelling kept);
    empty or None means unrestricted and is returned as None."""
    if not value:
        return None
    seen: set[str] = set()
    cleaned: list[str] = []
    for entry in value:
        slug = validate_repo_path(entry.strip())
        if slug.lower() not in seen:
            seen.add(slug.lower())
            cleaned.append(slug)
    return cleaned


class CreateKeyBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scope: str = Field(pattern="^(read|operate|admin|merge|propose)$")
    expires_days: int | None = Field(default=None, gt=0, le=730)
    service: bool = False
    # Repositories the key may start work on; omitted or empty: unrestricted.
    allowed_repos: list[str] | None = Field(default=None, max_length=MAX_ALLOWED_REPOS)

    @field_validator("allowed_repos")
    @classmethod
    def _allowed_repos(cls, v: list[str] | None) -> list[str] | None:
        return _clean_allowed_repos(v)


class AllowedReposBody(BaseModel):
    # Empty or null lifts the restriction.
    allowed_repos: list[str] | None = Field(max_length=MAX_ALLOWED_REPOS)

    @field_validator("allowed_repos")
    @classmethod
    def _allowed_repos(cls, v: list[str] | None) -> list[str] | None:
        return _clean_allowed_repos(v)


def _serialize(row: Any) -> dict[str, Any]:
    d = asdict(row)
    for k in ("created_at", "expires_at", "last_used_at", "revoked_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    d["service"] = d["user_id"] is None
    if d.get("allowed_repos") is not None:
        d["allowed_repos"] = list(d["allowed_repos"])
    return d


def _refuse_restricted_caller(request: Request) -> None:
    name = restricted_key_name(request)
    if name is not None:
        raise HTTPException(
            status_code=403,
            detail=f"API key {name!r} is limited to a repository allowlist and may not mint or change keys",
        )


async def _caller(request: Request) -> tuple[str, Any | None]:
    """Resolve the caller's effective tier and (when a session) users-row.

    Key/master callers carry no users-row: their keys, if minted, are
    service-style unowned. Returns ``(tier, user_row_or_None)``.
    """
    session = getattr(request.state, "session", None)
    if session is not None:
        users_repo = getattr(request.app.state, "users_repo", None)
        user = await users_repo.get_by_sub(session.sub) if users_repo is not None else None
        return session.tier, user
    principal = getattr(request.state, "principal", None)
    if principal is not None:
        return principal.tier, None
    # Only AUTH_DEV_MODE gets here: full access, no identity (#518).
    return unidentified_caller_tier(request), None


@router.get("/apikeys")
async def list_keys(request: Request) -> dict[str, Any]:
    repo = request.app.state.api_keys_repo
    tier, user = await _caller(request)
    if tier == TIER_ADMIN:
        rows = await repo.list_all()
    elif user is not None:
        rows = await repo.list_for_user(user.id)
    else:
        rows = []
    # A key with an allowlist sees only keys within it, the keys it may revoke.
    rows = [r for r in rows if wider_than_allowlist(request, r.allowed_repos) is None]
    return {"keys": [_serialize(r) for r in rows]}


@router.post("/apikeys", status_code=201)
async def create_key(body: CreateKeyBody, request: Request) -> dict[str, Any]:
    repo = request.app.state.api_keys_repo
    _refuse_restricted_caller(request)
    tier, user = await _caller(request)

    if body.service:
        if tier != TIER_ADMIN:
            raise HTTPException(status_code=403, detail="service keys require admin tier")
        owner_id = None
    else:
        if user is None:
            # Key-/master-authenticated callers have no users-row to bind to;
            # their keys are unowned by definition (documented behavior).
            if tier != TIER_ADMIN:
                raise HTTPException(status_code=403, detail="key-authenticated minting requires admin tier")
            owner_id = None
        else:
            owner_id = user.id

    # `merge` is off the read < operate < admin ladder: it can relay a merge
    # no operator key can, so only an admin mints one. `propose` likewise: it
    # starts the work an owner accepts in a chat (owner proposals).
    if body.scope in ("merge", "propose") and tier != TIER_ADMIN:
        raise HTTPException(status_code=403, detail=f"{body.scope}-scope keys require admin tier")
    max_scope = _TIER_MAX_SCOPE.get(tier)
    if max_scope is None or _SCOPE_RANK[body.scope] > _SCOPE_RANK[max_scope]:
        raise HTTPException(status_code=403, detail=f"scope {body.scope!r} exceeds your tier")

    expires_at = None
    if body.expires_days is not None:
        expires_at = datetime.now(UTC) + timedelta(days=body.expires_days)

    actor = request_actor(request)
    row, plaintext = await repo.create(
        name=body.name,
        scope=body.scope,
        user_id=owner_id,
        created_by=actor,
        expires_at=expires_at,
        allowed_repos=body.allowed_repos,
    )
    bust_api_key_cache()
    config = request.app.state.config
    await emit_audit(
        getattr(request.app.state, "db", None),
        "apikey.created",
        actor=actor,
        details={
            "key_id": row.id,
            "name": row.name,
            "scope": row.scope,
            "service": owner_id is None,
            "allowed_repos": body.allowed_repos,
        },
        audit_log_path=config.audit_log_path,
    )
    return {**_serialize(row), "key": plaintext}


@router.put("/apikeys/{key_id}/allowed-repos")
async def set_allowed_repos(key_id: str, body: AllowedReposBody, request: Request) -> dict[str, Any]:
    """Replace a key's repository allowlist; an empty list lifts it.

    Same authority as revoking: the key's owner or an admin. Takes effect
    at once — the key cache is busted, not left to expire.
    """
    repo = request.app.state.api_keys_repo
    _refuse_restricted_caller(request)
    tier, user = await _caller(request)
    existing = await repo.get(key_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"key {key_id!r} not found")
    is_owner = user is not None and existing.user_id == user.id
    if tier != TIER_ADMIN and not is_owner:
        raise HTTPException(status_code=403, detail="only the key owner or an admin can change its repositories")
    row = await repo.set_allowed_repos(key_id, body.allowed_repos)
    if row is None:
        raise HTTPException(status_code=404, detail=f"key {key_id!r} not found")
    bust_api_key_cache()
    config = request.app.state.config
    await emit_audit(
        getattr(request.app.state, "db", None),
        "apikey.allowed_repos_changed",
        actor=request_actor(request),
        details={
            "key_id": key_id,
            "name": existing.name,
            "before": list(existing.allowed_repos) if existing.allowed_repos is not None else None,
            "after": body.allowed_repos,
        },
        audit_log_path=config.audit_log_path,
    )
    return _serialize(row)


@router.delete("/apikeys/{key_id}")
async def revoke_key(key_id: str, request: Request) -> dict[str, Any]:
    """Revoke a key: the key's owner or an admin. A key with an allowlist revokes only keys whose allowlist is
    within its own, never a key without one; any other key answers as one that does not exist."""
    repo = request.app.state.api_keys_repo
    tier, user = await _caller(request)
    existing = await repo.get(key_id)
    missing = HTTPException(status_code=404, detail=f"key {key_id!r} not found")
    if existing is None:
        raise missing
    wider = wider_than_allowlist(request, existing.allowed_repos)
    if wider is not None:
        await answer_as_missing(request, wider, missing, action="revoke an API key")
    is_owner = user is not None and existing.user_id == user.id
    if tier != TIER_ADMIN and not is_owner:
        raise HTTPException(status_code=403, detail="only the key owner or an admin can revoke")
    row = await repo.revoke(key_id)
    if row is None:
        # already revoked — idempotent success
        row = existing
    bust_api_key_cache()
    config = request.app.state.config
    await emit_audit(
        getattr(request.app.state, "db", None),
        "apikey.revoked",
        actor=request_actor(request),
        details={"key_id": key_id, "name": existing.name},
        audit_log_path=config.audit_log_path,
    )
    return _serialize(row)
