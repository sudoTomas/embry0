"""The world (spec 2026-10-25): where the player manages their characters and sends them on quests.

- ``GET /world``: the characters and where each is, the quest board, the
  parties out now, the finished quests waiting for the player's evaluation,
  the loot and the regions.
- ``GET /world/characters/{agent_type}``: one character's quest log and totals.
- ``GET /world/quests/{job_id}``: one quest as its scroll: what it did, the
  loot, the experience, the cost and time, and the player's evaluation.
- ``PUT /world/quests/{job_id}/evaluation``: the player's rating, or "merged".
- ``POST /world/quests/{job_id}/send-back``: a follow-up on the quest's pull
  request with the player's notes.
- ``POST /world/quests/{job_id}/reject``: the work is not wanted; optionally
  closes its pull request.

Sending a party is an owner proposal's accept (``POST /proposals/{id}/accept``)
or a job (``POST /jobs``) with Run with: the world adds no way to start work.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from embry0.api.deps import ensure_admin, request_actor
from embry0.api.repo_allowlist import enforce_repo_allowlist
from embry0.api.schemas import RunWithRequest
from embry0.api.v1.run_with import checked_run_with
from embry0.audit.actions import AuditAction
from embry0.audit.helpers import emit_audit
from embry0.services.run_with import apply_run_with
from embry0.services.world import (
    INBOX_DAYS,
    LOOT_DAYS,
    PLAYER_ACTORS,
    RETURN_SECONDS,
    WorldCache,
    character_entries,
    failure_words,
    job_entry,
    lead_entry,
    quest_title,
    quest_xp,
)

logger = structlog.get_logger(__name__)
router = APIRouter()

#: How many unexplored repositories the map's edge names.
FRONTIER = 60
#: How much of a quest's report its scroll carries.
REPORT_CHARS = 8_000
#: How long the scroll waits for the forge's numbers on a pull request.
PR_STATS_SECONDS = 5.0
_GITHUB_PR = re.compile(r"^https://github\.com/(?P<repo>[^/]+/[^/]+)/pull/(?P<number>\d+)")


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


@router.get("/world")
async def read_world(request: Request) -> dict[str, Any]:
    """Everything the guild hall and the map show, read in one go; the parties are always fresh."""
    repo = _state(request, "world_repo")
    progression = await _state(request, "progression").read()
    cache = _cache(request)
    now = datetime.now(UTC)
    jobs = await repo.jobs(returning_seconds=RETURN_SECONDS)
    ids = [str(j["job_id"]) for j in jobs]
    runs, links, last_events, leads, places, stats, inbox, loot = await asyncio.gather(
        repo.agent_runs(ids),
        repo.lead_links(ids),
        repo.last_event_at(ids),
        cache.get("leads", lambda: _leads(request)),
        cache.get("regions", lambda: _regions(request)),
        cache.get("stats", repo.stats),
        repo.inbox(days=INBOX_DAYS, player_actors=list(PLAYER_ACTORS)),
        repo.loot(days=LOOT_DAYS),
    )
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
    snapshot = progression.to_dict()
    return {
        "generated_at": _iso(now),
        "account": snapshot["account"],
        "skills": snapshot["skills"],
        "characters": character_entries(snapshot["characters"], parties, stats, now),
        "parties": parties,
        "quests": [lead_entry(lead) for lead in leads],
        "inbox": [
            {
                "job_id": r["job_id"],
                "repo": r["repo"],
                "area": r["area"],
                "title": quest_title(r["task"]),
                "outcome": _outcome(r),
                "leader": r["agent_type"] or "developer",
                "pr_url": r["pr_url"],
                "finished_at": _iso(r["finished_at"]),
                "cost_usd": round(float(r["total_cost_usd"] or 0), 4),
                "failure": failure_words(r["error_code"]),
            }
            for r in inbox
        ],
        "loot": [
            {
                "job_id": r["job_id"],
                "repo": r["repo"],
                "area": r["area"],
                "title": quest_title(r["task"]),
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
    entry = character_entries([character.to_dict()], [], stats, datetime.now(UTC))[0]
    return {
        **entry,
        "quest_log": [
            {
                "job_id": r["job_id"],
                "repo": r["repo"],
                "area": r["area"],
                "title": quest_title(r["task"]),
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
    match = _GITHUB_PR.match(pr_url or "")
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
    runs, approval, links, verification, pr = await asyncio.gather(
        repo.quest_runs(job_id),
        repo.merge_approval(job_id),
        repo.lead_links([job_id]),
        verifications.get(job_id) if verifications is not None else asyncio.sleep(0, result=None),
        _pr_stats(request, quest.get("pr_url")),
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
        "title": quest_title(quest.get("task")),
        "task": str(quest.get("task") or "")[:4_000],
        "status": quest.get("status"),
        "outcome": _outcome(quest),
        "template": quest.get("pipeline_template"),
        "lead_id": links.get(job_id),
        "report": report[:REPORT_CHARS],
        "report_truncated": len(report) > REPORT_CHARS,
        "failure": failure_words(quest.get("error_code")),
        "error_code": quest.get("error_code"),
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
        "xp": quest_xp(quest, runs),
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


class EvaluationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: Literal["good", "needs_work", "bad"] | None = None
    #: "merged" when the player merged the pull request (through its merge approval or the forge).
    verdict: Literal["merged"] | None = None
    note: str = Field(default="", max_length=4_000)


async def _audit(request: Request, action: AuditAction, job_id: str, **fields: Any) -> dict[str, Any]:
    """Record the player's evaluation of ``job_id`` and its audit row; the evaluation as it now stands."""
    actor = request_actor(request)
    row = await _state(request, "world_repo").rate(job_id, rated_by=actor, **fields)
    await emit_audit(
        getattr(request.app.state, "db", None),
        action,
        actor=actor,
        details={"job_id": job_id, **{k: v for k, v in fields.items() if k != "note"}},
        audit_log_path=getattr(getattr(request.app.state, "config", None), "audit_log_path", None),
    )
    return {
        "job_id": job_id,
        "rating": row.get("rating"),
        "verdict": row.get("verdict"),
        "note": row.get("note") or "",
        "followup_job_id": row.get("followup_job_id"),
        "by": row.get("rated_by"),
        "at": _iso(row.get("rated_at")),
    }


@router.put("/world/quests/{job_id}/evaluation")
async def evaluate_quest(job_id: str, body: EvaluationBody, request: Request) -> dict[str, Any]:
    """The player's rating of the work (good, needs work, bad, with a note), or that they merged it."""
    if body.rating is None and body.verdict is None and not body.note:
        raise HTTPException(status_code=422, detail="give a rating, a verdict or a note")
    quest = await _quest_or_404(request, job_id)
    if quest.get("status") in ("pending", "running", "awaiting_input", "paused"):
        raise HTTPException(status_code=409, detail="the quest is still out: evaluate it when it is back")
    return await _audit(request, "quest.evaluated", job_id, rating=body.rating, verdict=body.verdict, note=body.note)


class SendBackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str = Field(min_length=1, max_length=8_000, description="What the work still needs, in the player's words")
    run_with: RunWithRequest | None = None


@router.post("/world/quests/{job_id}/send-back", status_code=202)
async def send_back(job_id: str, body: SendBackBody, request: Request) -> dict[str, Any]:
    """Send the work back with notes: a follow-up job on the quest's own pull request branch."""
    quest = await _quest_or_404(request, job_id)
    match = _GITHUB_PR.match(str(quest.get("pr_url") or ""))
    if match is None:
        raise HTTPException(status_code=409, detail="the quest brought back no GitHub pull request to send back")
    run_with = await checked_run_with(request, body.run_with, match["repo"])
    dispatch = _state(request, "comment_dispatch")
    actor = request_actor(request)
    result = await dispatch.start_followup(
        match["repo"],
        int(match["number"]),
        request=f"sent back from the dashboard by {actor} with these notes:\n\n{body.notes}",
        dispatched_by=actor,
        title=f"[send-back] {match['repo']}#{match['number']}",
        kind="send_back",
        asks="the notes",
        pipeline_config=apply_run_with(None, run_with),
    )
    if not result.get("dispatched"):
        raise HTTPException(
            status_code=409, detail=f"not sent back: {str(result.get('reason') or 'refused').replace('_', ' ')}"
        )
    evaluation = await _audit(
        request,
        "quest.sent_back",
        job_id,
        verdict="sent_back",
        rating="needs_work",
        note=body.notes,
        followup_job_id=str(result["job_id"]),
    )
    _cache(request).drop("stats")
    return {**evaluation, "followup_job_id": result["job_id"]}


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="", max_length=4_000)
    #: Close the quest's open pull request, with the note as its last comment.
    close_pr: bool = False


@router.post("/world/quests/{job_id}/reject")
async def reject(job_id: str, body: RejectBody, request: Request) -> dict[str, Any]:
    """The work is not wanted: record it, and close its pull request when asked."""
    quest = await _quest_or_404(request, job_id)
    if body.close_pr:
        # Closing a pull request is a forge action as weighty as merging one,
        # and a merge already needs admin (merge approvals).
        await ensure_admin(request)
    closed = False
    match = _GITHUB_PR.match(str(quest.get("pr_url") or ""))
    if body.close_pr and match is not None and quest.get("delivery_state") != "merged":
        organizations = _state(request, "organizations_repo")
        org = await organizations.get(match["repo"].split("/", 1)[0])
        if org is None:
            raise HTTPException(
                status_code=409, detail=f"no registered organization holds {match['repo']}'s credential"
            )
        from embry0.api.v1.organizations import org_forge_client

        client = await org_forge_client(request, org)
        repo, number = match["repo"], int(match["number"])
        if body.note:
            await client.post(f"/repos/{repo}/issues/{number}/comments", repo=repo, json={"body": body.note})
        resp = await client.patch(f"/repos/{repo}/pulls/{number}", repo=repo, json={"state": "closed"})
        if resp.status_code >= 300:
            raise HTTPException(
                status_code=502, detail=f"the forge did not close the pull request ({resp.status_code})"
            )
        closed = True
    evaluation = await _audit(request, "quest.rejected", job_id, verdict="rejected", rating="bad", note=body.note)
    _cache(request).drop("stats")
    return {**evaluation, "closed_pr": closed}
