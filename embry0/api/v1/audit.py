"""Global audit read API (RAV-1056 Phase C).

Mounted behind the normal operator+ auth; tier logic lives in-handler:
admins (admin session, master key, admin-scope key) see every row, while
operator/read callers see only rows whose ``actor`` matches the strings
they could have produced via ``request_actor`` (session email, key-owner
email, or ``key:<name>`` for service keys).

A key with a repository allowlist reads, within that, only the rows about
the repositories it covers and the rows of its own about no repository
(:mod:`embry0.api.audit_allowlist`), narrowed in the query; the actions the
filter offers it are those of the rows it reads.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from embry0.api.actor_visibility import caller_scope
from embry0.api.audit_allowlist import audit_narrowing
from embry0.api.deps import get_audit_repo
from embry0.api.served import ServedRoute
from embry0.api.session import TIER_ADMIN
from embry0.audit.actions import AUDIT_ACTION_LABELS, RETIRED_AUDIT_ACTIONS, names_of
from embry0.orchestration.dispatch_actors import REPO_ALLOWLIST
from embry0.storage.repositories.audit import AuditRepository

router = APIRouter(route_class=ServedRoute)

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


REDACTED = "[redacted]"


def _secret_named(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _copied(value: Any, stack: list[tuple[Any, Any]]) -> Any:
    """``value`` itself, or for an object or a list an empty copy, put on ``stack`` for :func:`_redact` to fill."""
    filled: Any
    if isinstance(value, dict):
        filled = {}
    elif isinstance(value, list):
        filled = [None] * len(value)
    else:
        return value
    stack.append((value, filled))
    return filled


def _redact(details: dict[str, Any]) -> dict[str, Any]:
    """``details`` with the whole value of every secret-named key hidden, at any depth.

    Historical ``provider_config_updated`` / ``integration_config_updated``
    rows carry raw secrets in their update payloads; never echo those to a
    browser, even for admins. A secret-named key's value is hidden whatever
    it is (a string, an object, a list), and the walk goes into every object
    and every list, so ``{"updates": [{"api_key": ...}]}`` is redacted too.
    It keeps its own stack rather than recursing, so a value nested deeper
    than the interpreter's recursion limit is walked as well.
    """
    out: dict[str, Any] = {}
    # Each object or list still to copy, with the copy it fills.
    stack: list[tuple[Any, Any]] = [(details, out)]
    while stack:
        source, copy = stack.pop()
        if isinstance(source, dict):
            for key, value in source.items():
                copy[key] = REDACTED if _secret_named(str(key)) else _copied(value, stack)
        else:
            for index, value in enumerate(source):
                copy[index] = _copied(value, stack)
    return out


#: The caller's tier and its own actor strings; shared with every route that names an actor.
_caller_scope = caller_scope


@router.get("/audit/actions")
async def list_audit_actions(request: Request) -> AuditActionsResponse:
    """Every action an audit row can carry, with its label: the Audit filter's list.

    Served from ``embry0.audit.actions``, the registry the writers are typed
    against, so the filter can select every action that is recorded. The
    retired names older rows carry come last, each with the action that
    replaced it (``replaced_by``) and that action's label; a filter by the new
    name finds their rows too.

    To a key with a repository allowlist, only the actions of the rows it
    reads (:mod:`embry0.api.audit_allowlist`): an action whose rows, under it
    or under a name it replaced, are there for it, and a retired name some
    such row carries.
    """
    current = [AuditActionEntry(action=action, label=label) for action, label in AUDIT_ACTION_LABELS.items()]
    retired = [
        AuditActionEntry(action=old, label=AUDIT_ACTION_LABELS[new], replaced_by=new)
        for old, new in RETIRED_AUDIT_ACTIONS.items()
    ]
    tier, own = await _caller_scope(request)
    narrowing = await audit_narrowing(request, own)
    if narrowing is not None:
        present = await get_audit_repo(request).actions_present(
            actors=None if tier == TIER_ADMIN else own, hidden_actors=[REPO_ALLOWLIST], narrowing=narrowing
        )
        current = [entry for entry in current if present.intersection(names_of(entry.action))]
        retired = [entry for entry in retired if entry.action in present]
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

    # A key with an allowlist reads the rows about its repositories and its own (audit_allowlist), and never a
    # refusal's row (dispatch_actors.REPO_ALLOWLIST).
    narrowing = await audit_narrowing(request, own)
    hidden = [REPO_ALLOWLIST] if narrowing is not None else []
    rows = await repo.list_entries(
        actors=actors,
        action=action,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
        hidden_actors=hidden,
        narrowing=narrowing,
    )
    total = await repo.count_entries(
        actors=actors, action=action, since=since, until=until, hidden_actors=hidden, narrowing=narrowing
    )
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
