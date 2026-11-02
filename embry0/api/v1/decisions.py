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
from embry0.api.repo_allowlist import covered_slugs, key_covers
from embry0.api.v1.ui import ui_settings
from embry0.services.decisions import Decision, done, offered, ordered
from embry0.settings.layers import PerRepository
from embry0.settings.sections.leads import LeadsSettings

logger = structlog.get_logger(__name__)
router = APIRouter()

#: Pull requests read from the forge at once while building quests to review.
_PR_READS = 6
#: How many quests to review, the newest, read their pull request's changes from the forge: as many as the
#: inbox's page held. Every quest to review is read (#296), so this keeps the forge reads of a read from growing
#: with how many wait. An older one's card shows only its pull request's number, from its address, as any card
#: does when the forge does not answer in time.
_PR_STATS_ROWS = 30
#: How much of a finished job's report its card carries in ``details``.
REPORT_CHARS = 8_000


async def _empty() -> dict[str, Any]:
    return {}


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _offered(p: dict[str, Any], leads: PerRepository[LeadsSettings]) -> Decision:
    """A proposal as its decision card, its readiness read against its repository's ``leads.ask_min``."""
    return offered(p, ask_min=leads.repo(str(p.get("repo") or "") or None).ask_min)


async def _offers(request: Request, *, owner: str | None, repo: str | None) -> list[Decision]:
    from embry0.api.v1.proposals import _shown, feed, leads_policy, readable
    from embry0.services.proposals import for_owner

    try:
        proposals = for_owner(readable(request, await feed(request)), owner)
    except HTTPException:
        return []
    if repo:
        proposals = [p for p in proposals if p.repo.lower() == repo.lower()]
    leads = await leads_policy(request)
    return [_offered(p, leads) for p in await _shown(request, proposals, leads)]


def listed_to(request: Request, decision: Mapping[str, Any]) -> bool:
    """Whether :func:`list_decisions` lists ``decision``, or the row it is built from, to the caller: only when
    the caller's key covers its repository (:func:`~embry0.api.repo_allowlist.key_covers`), whatever its stage,
    a stage this module does not know included.

    A list hides the rest without a refusal: nothing was refused, so nothing is
    logged or audited (a chat reads the feed every minute). :func:`_reviews`
    filters by it, and so does a reader that took every decision whatever the
    caller's key, such as the asks feed, which counts a person's asks toward
    their limit before it hands out the ones the caller may read (#272).
    """
    return key_covers(request, decision.get("repo"))


async def done_inputs(
    request: Request, rows: Sequence[dict[str, Any]], *, report_chars: int = REPORT_CHARS
) -> list[dict[str, Any]]:
    """Each finished job row with what the card needs: its pull request, checks, done-when and brief.

    The jobs, their merge approvals, verifications and briefs are each read in
    one statement, however many rows (#296). ``rows`` come newest first: the
    first ``_PR_STATS_ROWS`` read their pull request's changes from the forge.
    Each report is cut to ``report_chars`` as it is read.
    """
    from embry0.api.v1.world import _outcome, _pr_stats
    from embry0.services.world import failure_words, quest_title

    state = request.app.state
    world, jobs = state.world_repo, getattr(state, "jobs_repo", None)
    verifications = getattr(state, "job_verifications_repo", None)
    briefs_repo = getattr(state, "job_briefs_repo", None)
    ids = [str(r["job_id"]) for r in rows]
    if not ids:
        return []
    gate = asyncio.Semaphore(_PR_READS)

    async def briefs_of() -> dict[str, dict[str, Any]]:
        if briefs_repo is None:
            return {}
        try:
            return dict(await briefs_repo.current(ids))
        except Exception:  # noqa: BLE001 - a card without its brief still shows its facts
            logger.warning("decisions_briefs_unread", exc_info=True)
            return {}

    async def pr_of(row: dict[str, Any]) -> dict[str, Any] | None:
        async with gate:
            return await _pr_stats(request, row.get("pr_url"))

    async def prs_of() -> list[dict[str, Any] | None]:
        return list(await asyncio.gather(*(pr_of(r) for r in rows[:_PR_STATS_ROWS])))

    job_rows, approvals, verified, briefs, prs = await asyncio.gather(
        jobs.card_rows(ids, report_chars=report_chars) if jobs is not None else _empty(),
        world.merge_approvals(ids),
        verifications.for_jobs(ids) if verifications is not None else _empty(),
        briefs_of(),
        prs_of(),
    )

    def one(row: dict[str, Any], pr: dict[str, Any] | None) -> dict[str, Any]:
        job_id = str(row["job_id"])
        approval, verification = approvals.get(job_id) or {}, verified.get(job_id)
        merged = {**row, **(job_rows.get(job_id) or {})}
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
            "approval_ask": _json(approval.get("ask")) or {},
            # Merge on the card merges through a pending merge approval, as the review bar does.
            "approval_id": approval.get("id") if approval.get("status") == "pending" else None,
            "brief": briefs.get(job_id) or {},
            "report": report[:report_chars],
        }

    return [one(row, prs[i] if i < len(prs) else None) for i, row in enumerate(rows)]


async def with_reports(request: Request, shown: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """``shown``, each quest to review with its report in ``details``, cut to ``REPORT_CHARS`` as ``GET /decisions``
    shows it: for a reader that built its decisions without their reports and hands a few of them out, as the asks
    feed does. One statement, for the quests to review in ``shown`` alone."""
    jobs = getattr(request.app.state, "jobs_repo", None)
    ids = [str(d["id"]).removeprefix("done:") for d in shown if d.get("stage") == "done"]
    if jobs is None or not ids:
        return list(shown)
    rows = await jobs.card_rows(ids, report_chars=REPORT_CHARS)

    def report(job_id: str) -> str:
        return str((rows.get(job_id) or {}).get("result_summary") or "")[:REPORT_CHARS]

    return [
        {**d, "details": report(str(d["id"]).removeprefix("done:"))} if d.get("stage") == "done" else dict(d)
        for d in shown
    ]


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


async def _reviews(request: Request, *, repo: str | None, report_chars: int) -> list[Decision]:
    from embry0.services.world import INBOX_DAYS, PLAYER_ACTORS

    world = getattr(request.app.state, "world_repo", None)
    if world is None:
        return []
    game = (await ui_settings(request)).game
    # Every one, not the inbox's page (#296): a quest to review left out reads as answered, and the ask loop would
    # withdraw its ask. Narrowed in SQL to the repositories the caller's key covers.
    rows = await world.inbox(
        days=INBOX_DAYS, player_actors=list(PLAYER_ACTORS), limit=None, repos=covered_slugs(request)
    )
    if repo:
        rows = [r for r in rows if str(r.get("repo") or "").lower() == repo.lower()]
    # The feed's own rule (listed_to), so what the list shows and what the feed hands out cannot drift apart.
    rows = [r for r in rows if listed_to(request, r)]
    inputs, owners = await asyncio.gather(
        done_inputs(request, rows, report_chars=report_chars), owners_of(request, rows)
    )
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


async def waiting(
    request: Request,
    *,
    stage: Literal["offered", "done", "all"] = "all",
    owner: str | None = None,
    repo: str | None = None,
    include_held: bool = True,
    report_chars: int = REPORT_CHARS,
) -> list[dict[str, Any]]:
    """Every decision waiting on a person, worst first, each with its open hand-off, none cut off by a page.

    What :func:`list_decisions` lists before ``for`` and ``limit``. Every
    decision is built before any is cut, so a reader that needs them all, as
    the asks do, reads them here: a page's limit must not decide whether a
    decision is open (#273). The quests to review are every finished job
    waiting for review within ``INBOX_DAYS``, not the inbox's newest 30
    (#296), read in a fixed number of statements however many wait. Each
    one's ``details`` carries ``report_chars`` of its report: a reader that
    shows no card, such as the ask loop, reads none (:func:`with_reports`).
    """
    offers, reviews = await asyncio.gather(
        _offers(request, owner=owner, repo=repo) if stage in ("offered", "all") else asyncio.sleep(0, result=[]),
        _reviews(request, repo=repo, report_chars=report_chars)
        if stage in ("done", "all")
        else asyncio.sleep(0, result=[]),
    )
    found = [d for d in [*offers, *reviews] if include_held or not d.held]
    return await with_assignees(request, [d.to_dict() for d in ordered(found)])


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
    shown = await waiting(request, stage=stage, owner=owner, repo=repo, include_held=include_held)
    if for_ == "me":
        shown = await mine(request, shown)
    shown = shown[:limit]
    return {"decisions": shown, "count": len(shown)}


@router.get("/decisions/{decision_id:path}")
async def get_decision(decision_id: str, request: Request) -> dict[str, Any]:
    """One decision by its id: ``offer:<proposal id>`` or ``done:<job id>``. One in a repository the caller's key
    does not cover answers as one that does not exist."""
    return await read_decision(request, decision_id)


async def read_decision(request: Request, decision_id: str, *, action: str | None = None) -> dict[str, Any]:
    """One decision by its id, for a read or, naming ``action``, for a route that acts on it.

    A decision in a repository the caller's key does not cover answers as one
    that does not exist (:func:`~embry0.api.repo_allowlist.hide_unless_covered`):
    a quest on offer as a proposal that is not open, a quest to review as a
    quest that is not there. An action's refusal is logged and audited.
    """
    stage, _, ref = decision_id.partition(":")
    if stage == "offer" and ref:
        from embry0.api.v1.proposals import _shown, leads_policy, readable_proposal

        leads = await leads_policy(request)
        proposal = await readable_proposal(request, ref, action=action)
        found = _offered((await _shown(request, [proposal], leads))[0], leads).to_dict()
        return (await with_assignees(request, [found]))[0]
    if stage == "done" and ref:
        from embry0.api.v1.world import _quest_or_404

        quest = await _quest_or_404(request, ref, action=action)
        game = (await ui_settings(request)).game
        found = done((await done_inputs(request, [quest]))[0], game=game).to_dict()
        return (await with_assignees(request, [found]))[0]
    raise HTTPException(status_code=404, detail="a decision id is offer:<proposal id> or done:<job id>")
