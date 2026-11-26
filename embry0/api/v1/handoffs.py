"""Hand-offs (spec 2026-10-27): send a decision to a teammate, who then decides it.

- ``GET /handoffs/people``: who a decision can be sent to (the users who sign in
  and the people with an email; ``/people`` is the people directory since #179).
  Which logins a person is named by is the people's identities, which an admin
  writes (``/people``); the GitHub login a user named themselves by,
  ``PUT /me/github-login``, was retired with ``users.github_login`` (#179).
- ``POST /decisions/{id}/send-to``, ``GET /handoffs``,
  ``POST /handoffs/{id}/return`` and ``/cancel``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from embry0.api.deployment_reads import deployment_hidden
from embry0.api.deps import ensure_admin, request_actor
from embry0.api.repo_allowlist import covered_slugs, hide_unless_covered, key_covers
from embry0.audit.actions import AuditAction
from embry0.audit.helpers import emit_audit
from embry0.services.handoffs import notify, view

logger = structlog.get_logger(__name__)
router = APIRouter()

#: The tiers that can sign in to the dashboard and so answer what they are sent.
_SIGNED_IN = ("operator", "admin")


def _can_be_sent(entry: dict[str, Any]) -> bool:
    """Someone who signs in, or a person (#179): a chat assistant can ask them, signed in or not."""
    return entry["tier"] in _SIGNED_IN or bool(entry.get("person"))


def _repo(request: Request, name: str) -> Any:
    repo = getattr(request.app.state, name, None)
    if repo is None:
        raise HTTPException(status_code=503, detail=f"{name} is not initialized")
    return repo


def _person(u: Any) -> dict[str, Any]:
    return {"id": u.id, "email": u.email, "name": u.name or u.email, "tier": u.tier, "person": False}


async def people_by_email(request: Request) -> dict[str, dict[str, Any]]:
    """Everyone a decision can name, by lowercased email: the users not disabled, and the people not disabled with an
    email (#179), a person's name over their user's. A person keeps their user's ``id`` and ``tier``, one with no
    sign-in has their own ``id`` and no tier. {} when neither can be read."""
    out: dict[str, dict[str, Any]] = {}
    users = getattr(request.app.state, "users_repo", None)
    people = getattr(request.app.state, "people_repo", None)
    try:
        if users is not None:
            out = {u.email.lower(): _person(u) for u in await users.people()}
        if people is not None:
            off = await users.disabled_ids() if users is not None else set()
            for p in await people.all():
                if not p.email or (p.user_id and p.user_id in off):
                    continue
                entry = out.get(p.email.lower()) or {"id": p.id, "email": p.email, "tier": ""}
                out[p.email.lower()] = {**entry, "name": p.name or p.email, "person": True}
    except Exception:  # noqa: BLE001 - a card without its assignee's name still shows the email
        logger.warning("people_unread", exc_info=True)
    return out


@router.get("/handoffs/people")
async def list_people(request: Request) -> dict[str, Any]:
    """The people a decision can be sent to: everyone who signs in to the dashboard, and every person with an email
    (#179), whom a chat assistant can ask. To a key with a repository allowlist, as a deployment with none
    (embry0.api.deployment_reads). It answered at ``/people`` until the people API took that path (#179)."""
    if deployment_hidden(request):
        return {"people": [], "count": 0}
    people = [
        {key: value for key, value in p.items() if key != "person"}
        for p in sorted((await people_by_email(request)).values(), key=lambda p: (str(p["name"]).lower(), p["id"]))
        if _can_be_sent(p)
    ]
    return {"people": people, "count": len(people)}


class SendBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The email of the person who should decide it.
    to: str = Field(min_length=3, max_length=320)
    note: str = Field(default="", max_length=2000)


@router.post("/decisions/{decision_id}/send-to", status_code=201)
async def send_decision(decision_id: str, body: SendBody, request: Request) -> dict[str, Any]:
    """Send a decision to a teammate: it is theirs to decide until they answer it or send it back."""
    return await hand_off(request, decision_id, to=body.to, note=body.note, actor=request_actor(request))


async def hand_off(request: Request, decision_id: str, *, to: str, note: str, actor: str) -> dict[str, Any]:
    """Open a hand-off of ``decision_id`` from ``actor`` to the person with email ``to``.

    The dashboard's Send to, and a chat's (where ``actor`` is the person who
    pressed the button, not the assistant's key).
    """
    from embry0.api.v1.decisions import read_decision

    decision = await read_decision(request, decision_id, action="hand off a decision")
    person = (await people_by_email(request)).get(to.strip().lower())
    if person is None or not _can_be_sent(person):
        raise HTTPException(status_code=422, detail="send it to someone who signs in to embry0 or is a person in it")
    if str(person["email"]).lower() == actor.lower():
        raise HTTPException(status_code=422, detail="it is yours already: answer it")
    row = await _repo(request, "handoffs_repo").send(
        decision_id=decision["id"],
        repo=str(decision.get("repo") or ""),
        title=str(decision.get("title") or ""),
        from_actor=actor,
        to_email=person["email"],
        note=note.strip(),
        now=datetime.now(UTC),
    )
    await emit_audit(
        getattr(request.app.state, "db", None),
        "handoff.created",
        actor=actor,
        details={"handoff_id": row["id"], "decision_id": decision["id"], "to": person["email"]},
        audit_log_path=getattr(request.app.state.config, "audit_log_path", None),
    )
    notify(request.app.state, "handoff.created", row)
    return view(row)


@router.get("/handoffs")
async def list_handoffs(
    request: Request,
    for_: Literal["me", "all"] = Query(default="me", alias="for", description="me: the open ones sent to you"),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    repo = _repo(request, "handoffs_repo")
    if for_ == "me":
        rows = await repo.open_to(request_actor(request))
    else:
        rows = await repo.recent(limit=limit, repos=covered_slugs(request))
    # A hand-off carries its decision's repository and title: a key sees those of the repositories it covers.
    shown = [view(r) for r in rows if key_covers(request, r.get("repo"))][:limit]
    return {"handoffs": shown, "count": len(shown)}


class CloseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="", max_length=2000)


async def _close(
    request: Request, handoff_id: str, state: str, *, who: tuple[str, ...], note: str = ""
) -> dict[str, Any]:
    """Close one open hand-off as ``state`` when the caller is one of ``who`` (else an admin); tell the chat."""
    repo = _repo(request, "handoffs_repo")
    row = await repo.get(handoff_id)
    if row is None or row.get("state") != "open":
        raise HTTPException(status_code=404, detail="no open hand-off with that id")
    # A hand-off in a repository the caller's key does not cover answers as one that is not open.
    await hide_unless_covered(
        request,
        row.get("repo"),
        HTTPException(status_code=404, detail="no open hand-off with that id"),
        action=f"{'return' if state == 'returned' else 'cancel'} a hand-off",
    )
    actor = request_actor(request).lower()
    if actor not in {str(row.get(k) or "").lower() for k in who}:
        await ensure_admin(request)
    closed: dict[str, Any] | None = await repo.close(
        handoff_id=handoff_id,
        state=state,
        outcome=note.strip() or None,
        by=request_actor(request),
        now=datetime.now(UTC),
    )
    if closed is None:
        raise HTTPException(status_code=409, detail="it was answered meanwhile")
    notify(request.app.state, "handoff.closed", closed)
    return closed


async def _audit(request: Request, action: AuditAction, closed: dict[str, Any]) -> dict[str, Any]:
    await emit_audit(
        getattr(request.app.state, "db", None),
        action,
        actor=request_actor(request),
        details={"handoff_id": closed["id"], "decision_id": closed["decision_id"]},
        audit_log_path=getattr(request.app.state.config, "audit_log_path", None),
    )
    return view(closed)


@router.post("/handoffs/{handoff_id}/return")
async def return_handoff(handoff_id: str, request: Request, body: CloseBody | None = None) -> dict[str, Any]:
    """Not mine to decide: back to whoever sent it (the recipient, or an admin)."""
    closed = await _close(request, handoff_id, "returned", who=("to_email",), note=body.note if body else "")
    return await _audit(request, "handoff.returned", closed)


@router.post("/handoffs/{handoff_id}/cancel")
async def cancel_handoff(handoff_id: str, request: Request) -> dict[str, Any]:
    """Take it back (the sender, or an admin)."""
    closed = await _close(request, handoff_id, "cancelled", who=("from_actor",))
    return await _audit(request, "handoff.cancelled", closed)
