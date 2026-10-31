"""Decisions (spec 2026-10-27): every choice waiting on a person, in one shape.

``GET /decisions`` lists the quests on offer (the owner proposals feed) and the
quests to review (the world's inbox), each as a decision
(:mod:`embry0.services.decisions`). The actions a decision lists stay on their
own endpoints; this module only reads.

While the settings document turns the game layer off (``ui.game``, #177), a
finished job's card is labelled ``Job done`` instead of ``Quest done``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

import structlog
from fastapi import APIRouter, HTTPException, Query, Request

from embry0.api.deps import request_actor
from embry0.api.repo_allowlist import refusal_detail
from embry0.api.v1.ui import ui_settings
from embry0.services.decisions import Decision, done, offered, ordered

logger = structlog.get_logger(__name__)
router = APIRouter()

#: Pull requests read from the forge at once while building quests to review.
_PR_READS = 6
_REPORT_CHARS = 8_000


async def _none() -> None:
    return None


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


async def _offers(request: Request, *, owner: str | None, repo: str | None) -> list[Decision]:
    from embry0.api.v1.proposals import _shown, feed
    from embry0.services.proposals import for_owner

    try:
        proposals = for_owner(await feed(request), owner)
    except HTTPException:
        return []
    if repo:
        proposals = [p for p in proposals if p.repo.lower() == repo.lower()]
    return [offered(p) for p in await _shown(request, proposals)]


def _allowed(request: Request, repo: str | None) -> bool:
    """Whether the caller's key may read ``repo``'s decisions. A list hides the rest without a refusal:
    nothing was refused, so nothing is logged or audited (a chat reads the feed every minute)."""
    return refusal_detail(request, repo, action="read a decision") is None


def listed_to(request: Request, decision: Mapping[str, Any]) -> bool:
    """Whether :func:`list_decisions` lists ``decision`` to the caller, by the key rule it applies.

    :func:`_reviews` leaves out a quest to review in a repository the caller's
    key does not cover; :func:`_offers` leaves out nothing by key. For a reader
    that took every decision whatever the caller's key, such as the asks feed,
    which counts a person's asks toward their limit before it hands out the
    ones the caller may read (#272).
    """
    return decision.get("stage") != "done" or _allowed(request, decision.get("repo"))


async def done_inputs(request: Request, rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each finished job row with what the card needs: its pull request, checks, done-when and brief."""
    from embry0.api.v1.world import _outcome, _pr_stats
    from embry0.services.world import failure_words, quest_title

    state = request.app.state
    world, jobs = state.world_repo, getattr(state, "jobs_repo", None)
    verifications = getattr(state, "job_verifications_repo", None)
    briefs_repo = getattr(state, "job_briefs_repo", None)
    ids = [str(r["job_id"]) for r in rows]
    briefs: dict[str, dict[str, Any]] = {}
    if briefs_repo is not None and ids:
        try:
            briefs = await briefs_repo.current(ids)
        except Exception:  # noqa: BLE001 - a card without its brief still shows its facts
            logger.warning("decisions_briefs_unread", exc_info=True)
    gate = asyncio.Semaphore(_PR_READS)

    async def one(row: dict[str, Any]) -> dict[str, Any]:
        job_id = str(row["job_id"])
        async with gate:
            job, approval, verification, pr = await asyncio.gather(
                jobs.get(job_id) if jobs is not None else _none(),
                world.merge_approval(job_id),
                verifications.get(job_id) if verifications is not None else _none(),
                _pr_stats(request, row.get("pr_url")),
            )
        merged = {**row, **(job or {})}
        started, finished = merged.get("started_at") or merged.get("created_at"), merged.get("finished_at")
        seconds = (finished - started).total_seconds() if started is not None and finished is not None else None
        report = str(merged.get("result_summary") or "")
        return {
            "job_id": job_id,
            "repo": merged.get("repo"),
            "area": merged.get("area"),
            "title": quest_title(merged.get("task")),
            "task": merged.get("task"),
            "outcome": _outcome(merged),
            "pr_url": merged.get("pr_url"),
            "template": merged.get("pipeline_template"),
            "finished_at": finished,
            "cost_usd": round(float(merged.get("total_cost_usd") or 0), 4),
            "seconds": seconds,
            "failure": failure_words(merged.get("error_code"), merged.get("error_message")),
            "work_nature": _json(merged.get("work_nature")),
            "done_when": _json(merged.get("done_when")),
            "pr": pr,
            "verification": verification if isinstance(verification, dict) else None,
            "approval_ask": _json((approval or {}).get("ask")) or {},
            # Merge on the card merges through a pending merge approval, as the review bar does.
            "approval_id": (approval or {}).get("id") if (approval or {}).get("status") == "pending" else None,
            "brief": briefs.get(job_id) or {},
            "report": report[:_REPORT_CHARS],
        }

    return list(await asyncio.gather(*(one(r) for r in rows)))


async def owners_of(request: Request, rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Each finished quest's owners, as a quest on offer's are: its area's, else its repository's, else CODEOWNERS'."""
    from embry0.api.v1.proposals import _areas, _codeowners

    proposals_repo = getattr(request.app.state, "proposals_repo", None)
    if proposals_repo is None or not rows:
        return {}
    repos = {str(r.get("repo") or "").lower() for r in rows if r.get("repo")}
    try:
        set_owners = await proposals_repo.owners()
        codeowners, areas = await asyncio.gather(_codeowners(request, repos - set(set_owners)), _areas(request, repos))
    except Exception:  # noqa: BLE001 - a card without owners is asked of nobody, and still shown
        logger.warning("decisions_owners_unread", exc_info=True)
        return {}
    out: dict[str, list[str]] = {}
    for r in rows:
        name = str(r.get("repo") or "").lower()
        area = next((a for a in areas.get(name, []) if a.name == r.get("area") and a.owners), None)
        out[str(r["job_id"])] = list(area.owners if area else set_owners.get(name) or codeowners.get(name) or [])
    return out


async def _reviews(request: Request, *, repo: str | None) -> list[Decision]:
    from embry0.services.world import INBOX_DAYS, PLAYER_ACTORS

    world = getattr(request.app.state, "world_repo", None)
    if world is None:
        return []
    game = (await ui_settings(request)).game
    rows = await world.inbox(days=INBOX_DAYS, player_actors=list(PLAYER_ACTORS))
    if repo:
        rows = [r for r in rows if str(r.get("repo") or "").lower() == repo.lower()]
    rows = [r for r in rows if _allowed(request, r.get("repo"))]
    inputs, owners = await asyncio.gather(done_inputs(request, rows), owners_of(request, rows))
    return [done({**q, "owners": owners.get(str(q["job_id"]), [])}, game=game) for q in inputs]


async def with_assignees(request: Request, shown: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each decision's open hand-off as ``assigned_to`` (spec 2026-10-27), else None."""
    from embry0.api.v1.handoffs import people_by_email
    from embry0.services.handoffs import assignee

    repo = getattr(request.app.state, "handoffs_repo", None)
    if repo is None or not shown:
        return shown
    try:
        open_ = await repo.open_for(d["id"] for d in shown)
    except Exception:  # noqa: BLE001 - the cards still show without who has them
        logger.warning("decisions_handoffs_unread", exc_info=True)
        return shown
    people = await people_by_email(request) if open_ else {}
    return [{**d, "assigned_to": assignee(open_[d["id"]], people) if d["id"] in open_ else None} for d in shown]


async def _my_login(request: Request) -> str | None:
    users = getattr(request.app.state, "users_repo", None)
    if users is None:
        return None
    try:
        me = await users.get_by_email(request_actor(request))
    except Exception:  # noqa: BLE001 - without a login, owned quests are not known
        return None
    return me.github_login if me is not None else None


async def mine(request: Request, shown: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What waits for the viewer: handed to them, or on offer, owned by their login, and handed to nobody."""
    me = request_actor(request).lower()
    login = (await _my_login(request) or "").lower()
    out = []
    for d in shown:
        to = (d.get("assigned_to") or {}).get("email")
        if to:
            if to == me:
                out.append(d)
        elif login and d.get("stage") == "offered" and login in {o.lower().lstrip("@") for o in d.get("owners") or []}:
            out.append(d)
    return out


@router.get("/decisions")
async def list_decisions(
    request: Request,
    stage: Literal["offered", "done", "all"] = Query(default="all", description="offered, done or all"),
    owner: str | None = Query(default=None, description="A GitHub login: only the quests on offer it owns"),
    repo: str | None = Query(default=None, description="owner/name: only this repository's"),
    include_held: bool = Query(default=True, description="Also what a chat does not ask (shown on the page)"),
    for_: Literal["me", "all"] = Query(
        default="all", alias="for", description="me: handed to you, or on offer, owned by your GitHub login"
    ),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Every decision waiting on a person: the quests on offer and the quests to review, worst first."""
    offers, reviews = await asyncio.gather(
        _offers(request, owner=owner, repo=repo) if stage in ("offered", "all") else asyncio.sleep(0, result=[]),
        _reviews(request, repo=repo) if stage in ("done", "all") else asyncio.sleep(0, result=[]),
    )
    found = [d for d in [*offers, *reviews] if include_held or not d.held]
    shown = await with_assignees(request, [d.to_dict() for d in ordered(found)])
    if for_ == "me":
        shown = await mine(request, shown)
    shown = shown[:limit]
    return {"decisions": shown, "count": len(shown)}


@router.get("/decisions/{decision_id:path}")
async def get_decision(decision_id: str, request: Request) -> dict[str, Any]:
    """One decision by its id: ``offer:<proposal id>`` or ``done:<job id>``."""
    stage, _, ref = decision_id.partition(":")
    if stage == "offer" and ref:
        from embry0.api.v1.proposals import _open, _shown

        found = offered((await _shown(request, [await _open(request, ref)]))[0]).to_dict()
        return (await with_assignees(request, [found]))[0]
    if stage == "done" and ref:
        from embry0.api.v1.world import _quest_or_404

        quest = await _quest_or_404(request, ref)
        game = (await ui_settings(request)).game
        found = done((await done_inputs(request, [quest]))[0], game=game).to_dict()
        return (await with_assignees(request, [found]))[0]
    raise HTTPException(status_code=404, detail="a decision id is offer:<proposal id> or done:<job id>")
