"""Asks (spec 2026-10-27): the feed a chat assistant sends from, and the answers it passes back.

- ``GET /asks/due``: the asks to send now, each with its decision rendered,
  its code and the actions a chat may offer; a reminder is marked as one.
  Empty while ``asks.enabled`` is false in the settings document, which is
  read on every request, each ask by its decision's organization's values.
- ``POST /asks/{id}/sent``: the assistant sent it (``message_ref`` names the message).
- ``POST /asks/answer``: a button was pressed: the code, the action and the
  person who pressed it. The answer goes through the paths the dashboard
  uses; a second press returns the first answer.
- ``GET /asks``: the latest asks, for the dashboard.

A ``propose`` key does all of these: no model sits between a press and its answer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from embry0.api.schemas.person_name import PersonName
from embry0.services.asks import AskPolicy, release
from embry0.settings.sections.asks import ASKS, AsksSettings
from embry0.settings.store import Scoped, SettingsStore

logger = structlog.get_logger(__name__)
router = APIRouter()

#: What a chat may answer by itself; everything else opens the dashboard.
CHAT_ACTIONS = {"offered": ("start", "decline", "later", "send_to"), "done": ("merge", "send_to")}
#: How long a chat's "Later" waits, as the dashboard's does.
LATER_DAYS = 7


def _repo(request: Request, name: str) -> Any:
    repo = getattr(request.app.state, name, None)
    if repo is None:
        raise HTTPException(status_code=503, detail=f"{name} is not initialized")
    return repo


async def ask_policy(request: Request) -> Scoped[AsksSettings]:
    """The ``asks`` section of the settings document as it stands now, read per repository."""
    store: SettingsStore | None = getattr(request.app.state, "settings", None)
    if store is None:
        raise HTTPException(status_code=503, detail="settings storage not initialized")
    return (await store.snapshot()).scoped(ASKS)


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def chat_actions(decision: dict[str, Any]) -> list[str]:
    """The decision's actions a chat button may take; a merge only through a pending merge approval."""
    allowed = CHAT_ACTIONS.get(str(decision.get("stage")), ())
    acts = [a for a in decision.get("actions") or [] if a in allowed]
    if "merge" in acts and not (decision.get("links") or {}).get("merge_approval"):
        acts.remove("merge")
    return acts


def dashboard_link(request: Request, decision: dict[str, Any]) -> str:
    base = str(getattr(request.app.state.config, "dashboard_public_url", "") or "").rstrip("/")
    stage = "offered" if decision.get("stage") == "offered" else "done"
    return f"{base}/decisions?stage={stage}" if base else ""


async def _person(request: Request, who: str) -> dict[str, Any]:
    users = getattr(request.app.state, "users_repo", None)
    if who.startswith("github:"):
        return {"github_login": who.removeprefix("github:"), "email": None, "name": who.removeprefix("github:")}
    found = await users.get_by_email(who) if users is not None else None
    return {
        "email": who,
        "name": (found.name if found is not None else "") or who,
        "github_login": found.github_login if found is not None else None,
    }


async def open_decisions(request: Request) -> list[dict[str, Any]]:
    """Every open decision, worst first, read once for a whole feed."""
    from embry0.api.v1.decisions import list_decisions

    listed = await list_decisions(request, stage="all", owner=None, repo=None, include_held=True, for_="all", limit=500)
    return list(listed["decisions"])


@router.get("/asks/due")
async def asks_due(request: Request, limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    """The asks to send now: due reminders, then queued ones, worst first, while their person holds
    fewer than ``asks.max_open_per_person`` unanswered asks about that organization's decisions. Each
    ask is held to its decision's organization's values: its working hours for a reminder, its
    ``remind_hours`` and its limit. An empty list while ``asks.enabled`` is false in the settings
    document, whatever is stored; ``enabled`` says which."""
    section = await ask_policy(request)
    if not section.deployment().enabled:
        return {"asks": [], "count": 0, "enabled": False}
    asks = _repo(request, "asks_repo")
    now = datetime.now(UTC)
    decisions = await open_decisions(request)
    policy = AskPolicy(section, decisions)
    by_id = {str(d["id"]): d for d in decisions}
    order = {str(d["id"]): i for i, d in enumerate(decisions)}
    # Only what the caller may read is sent. An ask whose decision is not listed here may have been
    # answered, or may sit in a repo the caller's key does not cover: the ask loop, which reads every
    # decision, is what withdraws it.
    live = [a for a in await asks.live() if str(a["decision_id"]) in by_id]
    out: list[dict[str, Any]] = []
    for ask, reminder in release(live, order, now, policy):
        decision = by_id[str(ask["decision_id"])]
        out.append(
            {
                "id": ask["id"],
                "code": await asks.code(str(ask["decision_id"])),
                "recipient": await _person(request, str(ask["recipient"])),
                "reminder": reminder,
                "decision": decision,
                "actions": chat_actions(decision),
                "dashboard_url": dashboard_link(request, decision),
            }
        )
        if len(out) >= limit:
            break
    return {"asks": out, "count": len(out), "enabled": True}


class SentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_ref: str = Field(default="", max_length=500)


@router.post("/asks/{ask_id}/sent")
async def ask_sent(ask_id: str, body: SentBody, request: Request) -> dict[str, Any]:
    """The assistant sent the ask (or its reminder)."""
    row = await _repo(request, "asks_repo").sent(ask_id, message_ref=body.message_ref, now=datetime.now(UTC))
    if row is None:
        raise HTTPException(status_code=409, detail="that ask is not waiting to be sent")
    return {k: _iso(v) for k, v in row.items()}


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=4, max_length=16)
    action: Literal["start", "decline", "later", "send_to", "merge"]
    #: The email of the person who pressed the button (the assistant checked who they are).
    #: Never an ``embry0:`` actor's name.
    person: PersonName = Field(min_length=3, max_length=320)
    reason: str = Field(default="", max_length=2000)
    #: For send_to: the email of the teammate, and a note.
    to: str = Field(default="", max_length=320)
    note: str = Field(default="", max_length=2000)


async def _may_decide(request: Request, decision: dict[str, Any], person: str) -> bool:
    """The hand-off's person while one is open; else an owner (by GitHub login) or an admin."""
    users = getattr(request.app.state, "users_repo", None)
    me = await users.get_by_email(person) if users is not None else None
    held = (decision.get("assigned_to") or {}).get("email")
    if held:
        return held == person.lower() or (me is not None and me.tier == "admin")
    if me is None:
        return False
    owners = {str(o).lstrip("@").lower() for o in decision.get("owners") or []}
    return me.tier == "admin" or (bool(me.github_login) and me.github_login.lower() in owners)


async def _do(request: Request, decision: dict[str, Any], body: AnswerBody) -> dict[str, Any]:
    """Take the action through the path the dashboard uses, as ``body.person``."""
    from embry0.api.v1 import proposals

    links = decision.get("links") or {}
    if body.action == "send_to":
        from embry0.api.v1.handoffs import hand_off

        return await hand_off(request, str(decision["id"]), to=body.to, note=body.note, actor=body.person)
    if body.action == "merge":
        from embry0.services.handoffs import settle
        from embry0.services.merge_approvals import DecisionRefused

        service = _repo(request, "merge_approvals")
        try:
            # The gate above stood in for the dashboard's admin gate: an owner, the hand-off's person, or an admin.
            result = await service.decide(
                str(links["merge_approval"]),
                decision="approve",
                decided_by=body.person,
                chat_id="asks",
                via="dashboard",
            )
        except DecisionRefused as refused:
            raise HTTPException(status_code=refused.status_code, detail=refused.detail) from None
        await settle(request.app.state, str(decision["id"]), "merge", body.person)
        return dict(result)
    proposal_id = str(links.get("proposal") or "")
    if body.action == "start":
        return await proposals.accept_proposal(proposal_id, proposals.AcceptBody(by=body.person), request)
    if body.action == "decline":
        return await proposals.decline_proposal(
            proposal_id, proposals.AnswerBody(by=body.person, reason=body.reason), request
        )
    until = datetime.now(UTC) + timedelta(days=LATER_DAYS)
    return await proposals.snooze_proposal(proposal_id, proposals.SnoozeBody(by=body.person, until=until), request)


@router.post("/asks/answer")
async def answer_ask(body: AnswerBody, request: Request) -> dict[str, Any]:
    """A button pressed in a chat: answered once, through the dashboard's paths; a second press returns the first."""
    from embry0.api.v1.decisions import get_decision

    asks = _repo(request, "asks_repo")
    decision_id = await asks.decision_of(body.code.strip().upper())
    if decision_id is None:
        raise HTTPException(status_code=404, detail="no decision has that code")
    last = await asks.last_answer(decision_id)
    try:
        decision = await get_decision(decision_id, request)
    except HTTPException:
        if last is not None:
            return {"already": True, "answer": last.get("answer"), "by": last.get("answered_by"), "asks": []}
        raise HTTPException(status_code=409, detail="that quest was answered or moved on elsewhere") from None
    if body.action not in chat_actions(decision):
        raise HTTPException(status_code=409, detail=f"{body.action} is not open on that quest from a chat")
    if not await _may_decide(request, decision, body.person):
        raise HTTPException(
            status_code=403, detail="it is not theirs to decide: an owner, the person it was sent to, or an admin"
        )
    result = await _do(request, decision, body)
    closed = await asks.answered(decision_id, answer=body.action, by=body.person.lower(), now=datetime.now(UTC))
    shown = [{"id": a["id"], "recipient": a["recipient"], "message_ref": a.get("message_ref")} for a in closed]
    notifier = getattr(request.app.state, "webhook_notifier", None)
    if notifier is not None:
        notifier.emit_soon(
            "ask.answered",
            {
                "decision_id": decision_id,
                "code": body.code.upper(),
                "answer": body.action,
                "by": body.person,
                "asks": shown,
            },
        )
    logger.info("ask_answered", decision_id=decision_id, action=body.action, by=body.person)
    return {"already": False, "answer": body.action, "by": body.person, "result": result, "asks": shown}


@router.get("/asks")
async def list_asks(request: Request, limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    rows = await _repo(request, "asks_repo").recent(limit=limit)
    return {"asks": [{k: _iso(v) for k, v in r.items()} for r in rows], "count": len(rows)}
