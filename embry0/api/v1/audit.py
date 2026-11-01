"""Global audit read API (RAV-1056 Phase C).

Mounted behind the normal operator+ auth; tier logic lives in-handler:
admins (admin session, master key, admin-scope key) see every row, while
operator/read callers see only rows whose ``actor`` matches the strings
they could have produced via ``request_actor`` (session email, key-owner
email, or ``key:<name>`` for service keys).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from embry0.api.actor_visibility import caller_scope
from embry0.api.deps import get_audit_repo
from embry0.api.repo_allowlist import REFUSAL_ACTOR, restricted_key_name
from embry0.api.session import TIER_ADMIN
from embry0.audit.actions import AUDIT_ACTION_LABELS, RETIRED_AUDIT_ACTIONS
from embry0.storage.repositories.audit import AuditRepository

router = APIRouter()

_SENSITIVE_KEY_PARTS = ("key", "token", "secret", "password")


class AuditEntry(BaseModel):
    id: int
    action: str
    actor: str
    details: dict[str, Any]
    issue_id: str | None = None
    created_at: datetime


class AuditListResponse(BaseModel):
    entries: list[AuditEntry]
    total: int


class AuditActionEntry(BaseModel):
    action: str
    label: str
    #: For a retired name, which older rows carry, the action that replaced it; null for a current one.
    replaced_by: str | None = None


class AuditActionsResponse(BaseModel):
    actions: list[AuditActionEntry]


def _redact(details: dict[str, Any]) -> dict[str, Any]:
    """Shallow-recursive redaction of secret-shaped values.

    Historical ``provider_config_updated`` / ``integration_config_updated``
    rows carry raw secrets in their update payloads; never echo those to a
    browser, even for admins.
    """
    out: dict[str, Any] = {}
    for k, v in details.items():
        if isinstance(v, dict):
            out[k] = _redact(v)
        elif any(part in k.lower() for part in _SENSITIVE_KEY_PARTS):
            out[k] = "[redacted]"
        else:
            out[k] = v
    return out


#: The caller's tier and its own actor strings; shared with every route that names an actor.
_caller_scope = caller_scope


@router.get("/audit/actions")
async def list_audit_actions() -> AuditActionsResponse:
    """Every action an audit row can carry, with its label: the Audit filter's list.

    Served from ``embry0.audit.actions``, the registry the writers are typed
    against, so the filter can select every action that is recorded. The
    retired names older rows carry come last, each with the action that
    replaced it (``replaced_by``) and that action's label; a filter by the new
    name finds their rows too.
    """
    current = [AuditActionEntry(action=action, label=label) for action, label in AUDIT_ACTION_LABELS.items()]
    retired = [
        AuditActionEntry(action=old, label=AUDIT_ACTION_LABELS[new], replaced_by=new)
        for old, new in RETIRED_AUDIT_ACTIONS.items()
    ]
    return AuditActionsResponse(actions=[*current, *retired])


@router.get("/audit")
async def list_audit(
    request: Request,
    actor: str | None = None,
    action: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    repo: AuditRepository = Depends(get_audit_repo),
) -> AuditListResponse:
    # asyncpg rejects naive-vs-aware comparisons against timestamptz.
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    if until is not None and until.tzinfo is None:
        until = until.replace(tzinfo=UTC)

    tier, own = await _caller_scope(request)
    if tier == TIER_ADMIN:
        actors = [actor] if actor else None
    else:
        if actor and actor not in own:
            return AuditListResponse(entries=[], total=0)
        actors = [actor] if actor else own

    # A key with an allowlist never reads a refusal's row back (embry0.api.repo_allowlist.REFUSAL_ACTOR).
    hidden = [REFUSAL_ACTOR] if restricted_key_name(request) is not None else []
    rows = await repo.list_entries(
        actors=actors, action=action, since=since, until=until, limit=limit, offset=offset, hidden_actors=hidden
    )
    total = await repo.count_entries(actors=actors, action=action, since=since, until=until, hidden_actors=hidden)
    entries = [
        AuditEntry(
            id=r["id"],
            action=r["action"],
            actor=r["actor"],
            details=_redact(r["details"] or {}),
            issue_id=r["issue_id"],
            created_at=r["created_at"],
        )
        for r in rows
    ]
    return AuditListResponse(entries=entries, total=total)
