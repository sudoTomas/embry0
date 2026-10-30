"""The world (spec 2026-10-25): where the player manages their characters and sends them on quests.

- ``GET /world``: the characters and where each is, the quest board and the
  kinds a quest can be, the parties out now, the finished quests waiting for
  the player's evaluation, the loot and the regions.
- ``GET /world/characters/{agent_type}``: one character's quest log and totals.
- ``GET /world/quests/{job_id}``: one quest as its scroll: what it did, the
  loot, the experience, the cost and time, and the player's evaluation.

Sending a party is an owner proposal's accept (``POST /proposals/{id}/accept``)
or a job (``POST /jobs``) with Run with: the world adds no way to start work.
Judging the work is reviewing the job (``embry0/api/v1/job_review.py``,
``/jobs/{job_id}/evaluation`` and the rest), which is not part of the game:
the ``/world/quests/{job_id}/…`` paths it had are its deprecated aliases.

Every route here answers 404 while the settings document turns the game
layer off (``ui.game``, ``embry0/api/v1/ui.py``); the review aliases do not.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from embry0.api.repo_allowlist import enforce_repo_allowlist
from embry0.api.v1.job_review import GITHUB_PR
from embry0.api.v1.ui import require_game
from embry0.services.lead_kinds import catalogue as lead_kind_catalogue
from embry0.services.slop import dungeons
from embry0.services.world import (
    CLEANED_DAYS,
    ERROR_MESSAGE_CHARS,
    INBOX_DAYS,
    LOOT_DAYS,
    PLAYER_ACTORS,
    RETURN_SECONDS,
    WorldCache,
    account_wait_view,
    character_entries,
    failure_words,
    job_entry,
    lead_entry,
    quest_fallbacks,
    quest_title,
    quest_xp,
)

logger = structlog.get_logger(__name__)
# The game's reads: 404 while the settings document turns the game layer off (ui.game, #177).
router = APIRouter(dependencies=[Depends(require_game)])

#: How many unexplored repositories the map's edge names.
FRONTIER = 60
#: How much of a quest's report its scroll carries.
REPORT_CHARS = 8_000
#: How long the scroll waits for the forge's numbers on a pull request.
PR_STATS_SECONDS = 5.0


def _state(request: Request, name: str) -> Any:
    value = getattr(request.app.state, name, None)
    if value is None:
        raise HTTPException(status_code=503, detail=f"{name} is not initialized")
    return value


def _cache(request: Request) -> WorldCache:
    cache = getattr(request.app.state, "world_cache", None)
    if cache is None:
        cache = request.app.state.world_cache = WorldCache()
    return cache


def _iso(at: Any) -> str | None:
    if not isinstance(at, datetime):
        return None
    return (at if at.tzinfo else at.replace(tzinfo=UTC)).isoformat()


def _outcome(row: dict[str, Any]) -> str:
    """A quest's ending in one word the quest log shows."""
    if row.get("delivery_state") == "merged" or row.get("status") == "pr_merged":
        return "merged"
    status = str(row.get("status") or "")
    if status in ("failed", "expired"):
        return "failed"
    if status == "cancelled":
        return "recalled"
    if status in ("pending", "running", "awaiting_input", "paused"):
        return "out"
    if row.get("pr_url"):
        return "closed" if row.get("delivery_state") == "closed" or status == "pr_closed" else "pr_open"
    return "done"


async def _leads(request: Request) -> list[dict[str, Any]]:
    from embry0.api.v1.proposals import feed

    try:
        return [p.to_dict() for p in await feed(request)]
    except HTTPException:
        return []


async def _meters(request: Request) -> list[dict[str, Any]]:
    """Every Slop-O-Meter score, by repository and area (none without the store)."""
    store = getattr(request.app.state, "slop_meter_repo", None)
    if store is None:
        return []
    try:
        return list(await store.scores())
    except Exception:  # noqa: BLE001 - a dungeon without a meter is still drawn
        logger.info("world_meters_unread", exc_info=True)
        return []


async def _regions(request: Request) -> dict[str, Any]:
    from embry0.api.v1.proposals import _areas

    repo = _state(request, "world_repo")
    rows = await repo.regions()
    names = {str(r["repo"]) for r in rows}
    try:
        areas = await _areas(request, {n.lower() for n in names})
    except Exception:  # noqa: BLE001 - a region whose areas cannot be read is drawn without towns
        logger.info("world_areas_unread", exc_info=True)
        areas = {}
    regions = [
        {
            "repo": r["repo"],
            "jobs": int(r["jobs"] or 0),
            "last_job_at": _iso(r["last_job_at"]),
            "owned": bool(r["owned"]),
            "watched": bool(r["watched"]),
            "areas": [{"name": a.name, "slug": a.slug} for a in areas.get(str(r["repo"]).lower(), [])],
        }
        for r in rows
    ]
    frontier = [n for n in await repo.frontier(FRONTIER + len(names)) if n not in names][:FRONTIER]
    return {"regions": regions, "frontier": frontier}


async def _brief_titles(request: Request, job_ids: list[str]) -> dict[str, str]:
    """Each job's brief title (spec 2026-10-26, job briefs), for the jobs that have a current one."""
    briefs = getattr(request.app.state, "job_briefs_repo", None)
    if briefs is None or not job_ids:
        return {}
    try:
        return {job_id: str(row["title"]) for job_id, row in (await briefs.current(job_ids)).items()}
    except Exception:  # noqa: BLE001 - the hall shows each task's own first line instead
        logger.warning("job_brief_titles_unread", exc_info=True)
        return {}


@router.get("/world")
async def read_world(request: Request) -> dict[str, Any]:
    """Everything the guild hall and the map show, read in one go; the parties are always fresh."""
    repo = _state(request, "world_repo")
    progression = await _state(request, "progression").read()
    cache = _cache(request)
    now = datetime.now(UTC)
    jobs = await repo.jobs(returning_seconds=RETURN_SECONDS)
    ids = [str(j["job_id"]) for j in jobs]
    runs, links, last_events, leads, places, stats, inbox, loot, cleaned, meter_rows = await asyncio.gather(
        repo.agent_runs(ids),
        repo.lead_links(ids),
        repo.last_event_at(ids),
        cache.get("leads", lambda: _leads(request)),
        cache.get("regions", lambda: _regions(request)),
        cache.get("stats", repo.stats),
        repo.inbox(days=INBOX_DAYS, player_actors=list(PLAYER_ACTORS)),
        repo.loot(days=LOOT_DAYS),
        cache.get("cleaned", lambda: repo.cleaned(days=CLEANED_DAYS)),
        cache.get("meters", lambda: _meters(request)),
    )
    quests = [lead_entry(lead) for lead in leads]
    titles = await _brief_titles(request, [*ids, *(str(r["job_id"]) for r in [*inbox, *loot])])
    parties = [
        job_entry(
            j,
            runs.get(str(j["job_id"]), []),
            lead_id=links.get(str(j["job_id"])),
            last_event_at=last_events.get(str(j["job_id"])),
            now=now,
        )
        for j in jobs
    ]
    for party in parties:
        party["title"] = titles.get(str(party["job_id"]), party["title"])
    snapshot = progression.to_dict()
    return {
        "generated_at": _iso(now),
        "account": snapshot["account"],
        "skills": snapshot["skills"],
        "characters": character_entries(snapshot["characters"], parties, stats, now),
        "parties": parties,
        "quests": quests,
        # Every kind a quest can be, with its names (embry0.services.lead_kinds).
        "lead_kinds": lead_kind_catalogue(),
        # Slop dungeon (spec 2026-10-25): each repository's and area's open
        # slop, its score, and the cleanups merged lately.
        "dungeons": dungeons(quests, cleaned, meter_rows),
        "inbox": [
            {
                "job_id": r["job_id"],
                "repo": r["repo"],
                "area": r["area"],
                "title": titles.get(str(r["job_id"])) or quest_title(r["task"]),
                "outcome": _outcome(r),
                "leader": r["agent_type"] or "developer",
                "pr_url": r["pr_url"],
                "finished_at": _iso(r["finished_at"]),
                "cost_usd": round(float(r["total_cost_usd"] or 0), 4),
                "failure": failure_words(r["error_code"], r.get("error_message")),
                "error_code": r["error_code"],
            }
            for r in inbox
        ],
        "loot": [
            {
                "job_id": r["job_id"],
                "repo": r["repo"],
                "area": r["area"],
                "title": titles.get(str(r["job_id"])) or quest_title(r["task"]),
                "pr_url": r["pr_url"],
                "merged": r["delivery_state"] == "merged",
                "at": _iso(r["delivery_state_at"] or r["finished_at"]),
            }
            for r in loot
        ],
        "recent": snapshot["recent"],
        **places,
    }


@router.get("/world/characters/{agent_type}")
async def read_character(agent_type: str, request: Request) -> dict[str, Any]:
    """One character: its sheet's numbers, its totals and its quest log, newest first."""
    progression = await _state(request, "progression").read()
    character = progression.characters.get(agent_type)
    if character is None:
        raise HTTPException(status_code=404, detail=f"no character {agent_type}")
    repo = _state(request, "world_repo")
    stats, log = await asyncio.gather(repo.stats(agent_type), repo.quest_log(agent_type))
    titles = await _brief_titles(request, [str(r["job_id"]) for r in log])
    entry = character_entries([character.to_dict()], [], stats, datetime.now(UTC))[0]
    return {
        **entry,
        "quest_log": [
            {
                "job_id": r["job_id"],
                "repo": r["repo"],
                "area": r["area"],
                "title": titles.get(str(r["job_id"])) or quest_title(r["task"]),
                "outcome": _outcome(r),
                "pr_url": r["pr_url"],
                "template": r["pipeline_template"],
                "started_at": _iso(r["created_at"]),
                "finished_at": _iso(r["finished_at"]),
                "cost_usd": round(float(r["cost_usd"] or 0), 4),
                "seconds": round(float(r["duration_ms"] or 0) / 1000, 1),
                "rating": r["rating"],
                "verdict": r["verdict"],
            }
            for r in log
        ],
    }


async def _pr_stats(request: Request, pr_url: str | None) -> dict[str, Any] | None:
    """What the forge says the pull request changed; None when it cannot say in time."""
    match = GITHUB_PR.match(pr_url or "")
    if match is None:
        return None
    repo, number = match["repo"], int(match["number"])
    organizations = getattr(request.app.state, "organizations_repo", None)
    org = await organizations.get(repo.split("/", 1)[0]) if organizations is not None else None
    if org is None:
        return None
    from embry0.api.v1.organizations import org_forge_client

    try:
        client = await org_forge_client(request, org)
        resp = await asyncio.wait_for(client.get(f"/repos/{repo}/pulls/{number}", repo=repo), PR_STATS_SECONDS)
    except Exception:  # noqa: BLE001 - the scroll shows the link without the numbers
        logger.info("world_pr_stats_unread", pr_url=pr_url, exc_info=True)
        return None
    if resp.status_code != 200:
        return None
    pr = resp.json()
    return {
        "number": number,
        "state": "merged" if pr.get("merged") else pr.get("state"),
        "files": pr.get("changed_files"),
        "additions": pr.get("additions"),
        "deletions": pr.get("deletions"),
        "commits": pr.get("commits"),
        "title": pr.get("title"),
    }


async def _quest_or_404(request: Request, job_id: str) -> dict[str, Any]:
    quest = await _state(request, "world_repo").quest(job_id)
    if quest is None:
        raise HTTPException(status_code=404, detail=f"no quest {job_id}")
    await enforce_repo_allowlist(request, quest.get("repo"), action="read a quest")
    return dict(quest)


@router.get("/world/quests/{job_id}")
async def read_quest(job_id: str, request: Request) -> dict[str, Any]:
    """A finished (or running) quest as its scroll."""
    quest = await _quest_or_404(request, job_id)
    repo = _state(request, "world_repo")
    verifications = getattr(request.app.state, "job_verifications_repo", None)
    rules = await _state(request, "progression").rules()
    runs, approval, links, verification, pr, titles = await asyncio.gather(
        repo.quest_runs(job_id),
        repo.merge_approval(job_id),
        repo.lead_links([job_id]),
        verifications.get(job_id) if verifications is not None else asyncio.sleep(0, result=None),
        _pr_stats(request, quest.get("pr_url")),
        _brief_titles(request, [job_id]),
    )
    party: dict[str, dict[str, Any]] = {}
    for run in runs:
        agent = str(run.get("agent_type") or "unknown")
        entry = party.setdefault(agent, {"agent_type": agent, "runs": 0, "cost_usd": 0.0, "seconds": 0.0, "failed": 0})
        entry["runs"] += 1
        entry["cost_usd"] = round(entry["cost_usd"] + float(run.get("cost_usd") or 0), 4)
        entry["seconds"] = round(entry["seconds"] + float(run.get("duration_ms") or 0) / 1000, 1)
        entry["failed"] += 1 if run.get("error_code") or run.get("result") not in (None, "success") else 0
    started, finished = quest.get("started_at") or quest.get("created_at"), quest.get("finished_at")
    report = str(quest.get("result_summary") or "")
    return {
        "job_id": job_id,
        "repo": quest.get("repo"),
        "area": quest.get("area"),
        "title": titles.get(job_id) or quest_title(quest.get("task")),
        "task": str(quest.get("task") or "")[:4_000],
        "status": quest.get("status"),
        "outcome": _outcome(quest),
        "template": quest.get("pipeline_template"),
        "lead_id": links.get(job_id),
        "report": report[:REPORT_CHARS],
        "report_truncated": len(report) > REPORT_CHARS,
        "failure": failure_words(quest.get("error_code"), quest.get("error_message")),
        "error_code": quest.get("error_code"),
        "error_message": str(quest.get("error_message") or "")[:ERROR_MESSAGE_CHARS] or None,
        "pr_url": quest.get("pr_url"),
        "pr": pr,
        "merge_approval": (
            {"id": approval["id"], "status": approval["status"], "pr_url": approval["pr_url"]} if approval else None
        ),
        "verification": (
            {"verdict": verification.get("verdict"), "summary": verification.get("summary")}
            if isinstance(verification, dict)
            else None
        ),
        "party": list(party.values()),
        # Runs that ran on a fallback model because their own model's account was paused.
        "fallbacks": quest_fallbacks(runs),
        # What a paused quest waits for: a spent account, until when.
        "account_wait": account_wait_view(quest),
        # Slop dungeon: the slopometer's verdict, and a cleanup's diff counts.
        "work_nature": quest.get("work_nature"),
        "xp": quest_xp(quest, runs, rules=rules),
        "cost_usd": round(float(quest.get("total_cost_usd") or 0), 4),
        "seconds": (finished - started).total_seconds()
        if isinstance(started, datetime) and isinstance(finished, datetime)
        else None,
        "started_at": _iso(started),
        "finished_at": _iso(finished),
        "evaluation": (
            {
                "rating": quest.get("rating"),
                "verdict": quest.get("verdict"),
                "note": quest.get("note") or "",
                "followup_job_id": quest.get("followup_job_id"),
                "by": quest.get("rated_by"),
                "at": _iso(quest.get("rated_at")),
            }
            if quest.get("rated_by")
            else None
        ),
    }
