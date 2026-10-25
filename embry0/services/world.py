"""The world (spec 2026-10-25): embry0's work as a game the person plays.

The characters are the agent types; a quest is a job; the quest board is the
owner proposals (and, later, the leads finders bring in); the loot is what a
quest brought back. Nothing here is new state: the world is read from jobs,
their event log, traces, owner proposal answers and progression, and the one
thing the player writes is their evaluation of a finished quest
(``job_ratings``).

This module holds the rules that turn those rows into the game: a lead's
level and verb, a job's party and state, a character's place in the world,
and what a finished quest earned.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from embry0.orchestration import dispatch_actors
from embry0.services.progression import OUTCOME_XP, price
from embry0.services.proposals import ACTOR as PROPOSALS_ACTOR
from embry0.storage.repositories.progression import ProgressEvent

#: How long a party that finished stays out, walking home, before it is back in the hall.
RETURN_SECONDS = 180.0
#: A party with no new event this long is stuck.
STUCK_SECONDS = 20 * 60.0
#: A character with no quest this long is idle too long.
IDLE_DAYS = 14
#: How far back the quest-complete inbox looks.
INBOX_DAYS = 14
#: How far back the loot chest looks.
LOOT_DAYS = 7
#: Service actors that act for a person: their finished jobs are the player's quests too.
PLAYER_ACTORS: tuple[str, ...] = (dispatch_actors.MASTER_KEY, PROPOSALS_ACTOR)
#: The highest level a lead or a character reaches.
MAX_LEVEL = 126
#: How long the heavy parts of the world (the quest board, the regions) are kept.
CACHE_SECONDS = 60.0


@dataclass(frozen=True)
class LeadKind:
    #: The button that sends a party: what the player does to it.
    verb: str
    #: What stands in the world for it.
    creature: str
    #: The character a quest of this kind is led by.
    leader: str


#: Every kind of lead the quest board knows; a kind not here reads as a mystery to investigate.
LEAD_KINDS: dict[str, LeadKind] = {
    "case": LeadKind("Attack", "case_bug", "developer"),
    "issue": LeadKind("Start quest", "quest_giver", "developer"),
    "finding": LeadKind("Repair", "audit_signpost", "context-auditor"),
    # The leads finders bring in (spec 2026-10-25, leads).
    "ticket": LeadKind("Start quest", "quest_giver", "developer"),
    "ci": LeadKind("Attack", "ci_golem", "developer"),
    "alert": LeadKind("Attack", "alert_wyrm", "developer"),
    "flaky": LeadKind("Attack", "flaky_imp", "developer"),
    "review": LeadKind("Talk", "review_ghost", "developer"),
}
UNKNOWN_KIND = LeadKind("Investigate", "mystery", "developer")

_SEVERITY = {"critical": 4, "high": 3, "medium": 2, "low": 1, "misleads": 2, "dead": 1, "drift": 1}
_BASE_LEVEL = {"finding": 9, "ci": 45, "alert": 40, "flaky": 30, "review": 12}

#: The character that leads a job of each template when no agent has started yet.
LEADER_BY_TEMPLATE: dict[str, str] = {
    "analysis-default": "analysis",
    "research-default": "research",
    "ops-default": "ops",
    "qa": "qa",
    "pr-review": "pr-reviewer",
    "onboard": "onboarding",
    "kintsugi-trace": "kintsugi",
    "garden": "gardener",
    "interrogate": "interrogator",
    "why": "why",
    "how": "how",
    "blast-radius": "blast-radius",
    "comment-pass": "comment-sicko",
    "design-audit": "design-auditor",
    "context-audit": "context-auditor",
    "map-areas": "area-mapper",
}

#: The template a quest the player writes for a character runs: its own, or None for the
#: developer's (triage picks the pipeline). A character missing here cannot be given a
#: free-form quest (triage, review and QA work only inside another quest).
QUEST_TEMPLATE_BY_AGENT: dict[str, str | None] = {
    "developer": None,
    **{
        agent: template
        for template, agent in LEADER_BY_TEMPLATE.items()
        if template not in ("qa", "pr-review", "onboard")
    },
}

#: Job statuses, as the world says them.
_FINISHED_STATE = {
    "failed": "defeated",
    "expired": "defeated",
    "cancelled": "recalled",
}


def _clamp(level: float) -> int:
    return max(1, min(MAX_LEVEL, int(round(level))))


def lead_kind(kind: str) -> LeadKind:
    return LEAD_KINDS.get(kind, UNKNOWN_KIND)


def lead_level(lead: Mapping[str, Any]) -> int:
    """A lead's difficulty, 1 to 126: how menacing it stands in the world.

    A lead that names its own level (a finder's estimate) keeps it. A watcher
    case grows with its severity and with how often it was seen; an issue or a
    ticket with how much it asks; the rest start from their kind's base.
    """
    named = lead.get("level")
    if isinstance(named, int | float) and not isinstance(named, bool):
        return _clamp(named)
    kind = str(lead.get("kind") or "")
    severity = _SEVERITY.get(str(lead.get("severity") or "").lower(), 2)
    if kind == "case":
        seen = max(0, int(lead.get("occurrences") or 0))
        return _clamp(10 + 8 * severity + 10 * math.log2(1 + seen))
    if kind in ("issue", "ticket"):
        asks = len(str(lead.get("details") or ""))
        labels = {str(label).lower() for label in lead.get("labels") or []}
        return _clamp(10 + min(40, asks // 150) + (10 if "bug" in labels else 0))
    base = _BASE_LEVEL.get(kind, 20)
    return _clamp(base + (8 * (severity - 2) if kind == "alert" else 0))


def lead_entry(proposal: Mapping[str, Any]) -> dict[str, Any]:
    """A proposal as the quest board shows it: its level, the verb that sends a party, and who leads it."""
    kind = lead_kind(str(proposal.get("kind") or ""))
    return {
        **proposal,
        "level": lead_level(proposal),
        "verb": kind.verb,
        "creature": kind.creature,
        "leader": kind.leader,
    }


def party_of(runs: Iterable[tuple[str, str]]) -> tuple[list[str], str | None]:
    """The agents a job's event log names, in the order they first set out, and the one working now.

    ``runs`` are ``(event, agent)`` pairs in order: ``agent_started`` and
    ``agent_completed``. The one working now is the latest started agent with
    a run still open; None between steps and before the first.
    """
    members: list[str] = []
    open_runs: dict[str, int] = {}
    current: str | None = None
    for event, agent in runs:
        if event == "agent_started":
            if agent not in members:
                members.append(agent)
            open_runs[agent] = open_runs.get(agent, 0) + 1
            current = agent
        elif event == "agent_completed":
            open_runs[agent] = max(0, open_runs.get(agent, 0) - 1)
            if current == agent and not open_runs[agent]:
                current = next((a for a in reversed(members) if open_runs.get(a)), None)
    return members, current


def job_state(status: str, current: str | None, members: list[str]) -> str:
    """Where a job's party is: travelling, working, waiting for the player, or back (returning, defeated, recalled)."""
    if status == "pending":
        return "travelling"
    if status in ("awaiting_input", "paused"):
        return "waiting"
    if status == "running":
        return "working" if current or members else "travelling"
    return _FINISHED_STATE.get(status, "returning")


def leader_of(job: Mapping[str, Any], members: list[str], current: str | None) -> str:
    """The character a job is drawn as: the one working now, else the last that worked other than triage, else its template's."""
    if current and current != "triage":
        return current
    worked = [m for m in members if m != "triage"]
    if worked:
        return worked[-1]
    template = str(job.get("pipeline_template") or "")
    if template in LEADER_BY_TEMPLATE:
        return LEADER_BY_TEMPLATE[template]
    if members:
        return current or members[-1]
    return "developer"


def quest_title(task: str | None) -> str:
    """A quest's name: the first line of its task, without the tracker and source tags it may start with."""
    line = (task or "").strip().splitlines()[0] if (task or "").strip() else "A quest"
    while line.startswith("[") and "]" in line:
        line = line[line.index("]") + 1 :].strip()
    return line[:140] or "A quest"


def _iso(at: Any) -> str | None:
    if not isinstance(at, datetime):
        return None
    return (at if at.tzinfo else at.replace(tzinfo=UTC)).isoformat()


def _seconds_since(at: Any, now: datetime) -> float | None:
    if not isinstance(at, datetime):
        return None
    return (now - (at if at.tzinfo else at.replace(tzinfo=UTC))).total_seconds()


#: The most of a job's error message the world sends, and of the provider's words a failure quotes.
ERROR_MESSAGE_CHARS = 2_000
_PROVIDER_WORDS_CHARS = 240


def provider_words(error_message: str | None) -> str | None:
    """The provider's own words at the end of a failed job's message, if it carries them.

    An account failure's message ends with ``"; <agent>: <the provider's text>"``
    (``_executor_outcomes``); a message with no such tail is the provider's text
    itself, or a summary, and is returned whole.
    """
    text = (error_message or "").strip()
    if not text:
        return None
    tail = text.rsplit("; ", 1)[-1]
    agent, sep, words = tail.partition(": ")
    if sep and agent and " " not in agent and words.strip():
        tail = words
    tail = " ".join(tail.split())
    return tail[:_PROVIDER_WORDS_CHARS] + ("…" if len(tail) > _PROVIDER_WORDS_CHARS else "")


def failure_words(error_code: str | None, error_message: str | None = None) -> str | None:
    """Why a quest failed, in plain words; None for one that did not.

    An account failure (a spent usage window, a rate limit, a refused token)
    names the provider's own words when the job kept them ("weekly limit ·
    resets ..."), not the generic list of what it might have been.
    """
    if not error_code:
        return None
    from embry0.safety.error_codes import ACCOUNT_FAILURE_CODES
    from embry0.services._executor_outcomes import _stopped_because

    words = provider_words(error_message) if error_code in ACCOUNT_FAILURE_CODES else None
    if words:
        return f"the LLM account it ran on refused it: {words}"
    return _stopped_because(error_code)


def account_wait_view(job: Mapping[str, Any]) -> dict[str, Any] | None:
    """What a paused job waits for (spent account waits): provider, reason, until (UTC ISO), model, agent, chosen."""
    raw = job.get("account_wait")
    if job.get("status") != "paused" or not isinstance(raw, Mapping) or not raw.get("until"):
        return None
    keys = ("provider", "reason", "until", "model", "agent", "chosen")
    return {key: raw.get(key) for key in keys}


def job_entry(
    job: Mapping[str, Any],
    runs: list[tuple[str, str]],
    *,
    lead_id: str | None,
    last_event_at: Any,
    now: datetime,
) -> dict[str, Any]:
    """One party: who leads it, who is in it, where it is, and whether it looks stuck."""
    members, current = party_of(runs)
    status = str(job.get("status") or "")
    state = job_state(status, current, members)
    silent = _seconds_since(last_event_at or job.get("started_at") or job.get("created_at"), now)
    return {
        "job_id": job["job_id"],
        "repo": job.get("repo"),
        "area": job.get("area"),
        "title": quest_title(job.get("task")),
        "status": status,
        "state": state,
        "leader": leader_of(job, members, current),
        "party": members,
        "working": current,
        "lead_id": lead_id,
        "template": job.get("pipeline_template"),
        "since": _iso(job.get("started_at") or job.get("created_at")),
        "finished_at": _iso(job.get("finished_at")),
        "pr_url": job.get("pr_url"),
        "delivery_state": job.get("delivery_state"),
        "stuck": state in ("working", "travelling") and silent is not None and silent > STUCK_SECONDS,
        # A waiting party held by a spent account, and until when.
        "account_wait": account_wait_view(job),
        "failure": failure_words(job.get("error_code"), job.get("error_message")) if state == "defeated" else None,
        # For a debug copy of a failure: the code and the job's own message, as stored.
        "error_code": job.get("error_code") if state == "defeated" else None,
        "error_message": (str(job.get("error_message") or "")[:ERROR_MESSAGE_CHARS] or None)
        if state == "defeated"
        else None,
    }


def _stats_entry(row: Mapping[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    quests = int(row.get("quests") or 0)
    succeeded = int(row.get("succeeded") or 0)
    duration = row.get("avg_duration_ms")
    cost = row.get("avg_cost_usd")
    return {
        "quests": quests,
        "succeeded": succeeded,
        "failed": int(row.get("failed") or 0),
        "merged": int(row.get("merged") or 0),
        "success_rate": round(succeeded / quests, 3) if quests else None,
        "avg_cost_usd": round(float(cost), 4) if cost is not None else None,
        "avg_seconds": round(float(duration) / 1000, 1) if duration is not None else None,
        "last_quest_at": _iso(row.get("last_quest_at")),
        "ratings": {
            "good": int(row.get("rated_good") or 0),
            "needs_work": int(row.get("rated_needs_work") or 0),
            "bad": int(row.get("rated_bad") or 0),
        },
    }


def character_entries(
    characters: Iterable[Mapping[str, Any]],
    parties: list[dict[str, Any]],
    stats: Mapping[str, Mapping[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    """Every character with where it is now: the party it leads (or is in), and how its quests have gone.

    A character on several quests at once is drawn at the newest one it
    leads, with the count; one on none is in the hall, idle too long when its
    last quest is more than :data:`IDLE_DAYS` old.
    """
    out = []
    for character in characters:
        agent = str(character["agent_type"])
        leading = [p for p in parties if p["leader"] == agent]
        along = [p for p in parties if agent in p["party"] and p["leader"] != agent]
        out_now = [p for p in leading + along if p["state"] in ("travelling", "working", "waiting")]
        primary = next(iter(out_now or leading or along), None)
        entry_stats = _stats_entry(stats.get(agent))
        since_last = _seconds_since(stats.get(agent, {}).get("last_quest_at"), now)
        state = "idle" if primary is None else ("following" if primary["leader"] != agent else primary["state"])
        out.append(
            {
                **character,
                "state": state,
                "job_id": primary["job_id"] if primary else None,
                "quests_out": len(out_now),
                "stats": entry_stats,
                "idle_too_long": primary is None and (since_last is None or since_last > IDLE_DAYS * 86_400),
                "quest_template": QUEST_TEMPLATE_BY_AGENT.get(agent),
                "can_quest": agent in QUEST_TEMPLATE_BY_AGENT,
            }
        )
    return out


def quest_fallbacks(runs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The models a quest's party ran on because its own model's account was paused (provider fallback).

    One entry per agent, stand-in model and model it replaced, in the order
    they first ran, with how many runs it covered.
    """
    entries: dict[tuple[str, str, str], dict[str, Any]] = {}
    for run in runs:
        record = run.get("fallback")
        if not isinstance(record, Mapping):
            continue
        agent = str(run.get("agent_type") or "unknown")
        model = str(run.get("model") or "")
        instead_of = str(record.get("instead_of") or "")
        entry = entries.setdefault(
            (agent, model, instead_of),
            {
                "agent_type": agent,
                "model": model,
                "instead_of": instead_of,
                "provider": record.get("provider"),
                "reason": record.get("reason"),
                "until": record.get("until"),
                "runs": 0,
            },
        )
        entry["runs"] += 1
    return list(entries.values())


def quest_xp(job: Mapping[str, Any], runs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """What a quest earned each character, by skill: its agents' runs, and the pull request it opened or merged."""
    gains: dict[tuple[str, str], int] = {}
    at = job.get("finished_at") or job.get("created_at") or datetime.now(UTC)
    events = [
        ProgressEvent(
            "run", r.get("agent_type"), r.get("created_at") or at, None, r.get("tools_called"), r.get("skill_actions")
        )
        for r in runs
        if r.get("agent_type") and not r.get("error_code") and r.get("result") == "success"
    ]
    if job.get("pr_url"):
        events.append(ProgressEvent("opened", "developer", at))
    if job.get("status") == "pr_merged" or job.get("delivery_state") == "merged":
        events.append(ProgressEvent("merged", "developer", at))
    for event in events:
        if event.source != "run" and event.source not in OUTCOME_XP:
            continue
        for stat, xp in price(event):
            key = (str(event.agent_type), stat)
            gains[key] = gains.get(key, 0) + xp
    return [
        {"agent_type": agent, "stat": stat, "xp": xp}
        for (agent, stat), xp in sorted(gains.items(), key=lambda item: (-item[1], item[0]))
    ]


class WorldCache:
    """Keeps the heavy parts of the world (the quest board, the regions) for :data:`CACHE_SECONDS`."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str, load: Callable[[], Awaitable[Any]]) -> Any:
        async with self._lock:
            cached = self._entries.get(key)
            now = self._clock()
            if cached is not None and now - cached[0] < CACHE_SECONDS:
                return cached[1]
        value = await load()
        async with self._lock:
            self._entries[key] = (self._clock(), value)
        return value

    def drop(self, key: str) -> None:
        self._entries.pop(key, None)
