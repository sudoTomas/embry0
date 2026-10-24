"""Progression (spec 2026-10-24): the work embry0 records, as experience and levels.

Every built-in agent trains one skill, and three skills belong to the
instance: Slayer (the watcher's healed cases), Mining (the watcher's log
reads) and Farming (the schedules' starts). An event the database already
holds is worth a fixed amount of experience in its skill; a skill's level
follows the classic curve from 1 to 99. Nothing is stored: the levels are
recomputed from the rows, so the same history always reads the same way.

In code a skill is a ``stat``: embry0's Skills page already means agent
skill packs.
"""

from __future__ import annotations

import asyncio
import math
import time
from bisect import bisect_right
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from embry0.services.context_audit import report_counts
from embry0.storage.repositories.progression import ProgressEvent

MAX_LEVEL = 99

#: ``trained_by`` of a skill no single agent trains.
INSTANCE = "instance"


def _xp_table() -> tuple[int, ...]:
    """The experience each level needs, levels 1 to 99: ``floor(sum floor(n + 300 * 2^(n/7)) / 4)``."""
    table, points = [0], 0
    for n in range(1, MAX_LEVEL):
        points += math.floor(n + 300 * 2 ** (n / 7))
        table.append(points // 4)
    return tuple(table)


XP_TABLE = _xp_table()


def xp_for_level(level: int) -> int:
    """The experience level ``level`` starts at, clamped to 1..99."""
    return XP_TABLE[max(1, min(level, MAX_LEVEL)) - 1]


def level_for_xp(xp: int) -> int:
    """The level ``xp`` experience reaches, 1 to 99."""
    return min(bisect_right(XP_TABLE, max(xp, 0)), MAX_LEVEL)


#: The gear an agent wears from its level, highest first: (from level, tier).
#: Spread over the curve's top, where experience piles up: a few months of an
#: active deployment reach the 80s, so Divine is kept for 90.
TIERS: tuple[tuple[int, str], ...] = (
    (90, "divine"),
    (75, "rune"),
    (60, "adamant"),
    (45, "mithril"),
    (30, "steel"),
    (15, "iron"),
    (1, "bronze"),
)


def tier_for_level(level: int) -> str:
    return next(tier for start, tier in TIERS if level >= start)


@dataclass(frozen=True)
class Stat:
    id: str
    name: str
    #: The agent type that trains it, or :data:`INSTANCE`.
    trained_by: str


STATS: tuple[Stat, ...] = (
    Stat("smithing", "Smithing", "developer"),
    Stat("defence", "Defence", "review"),
    Stat("herblore", "Herblore", "qa"),
    Stat("hunter", "Hunter", "triage"),
    Stat("magic", "Magic", "analysis"),
    Stat("runecraft", "Runecraft", "research"),
    Stat("construction", "Construction", "onboarding"),
    Stat("firemaking", "Firemaking", "ops"),
    Stat("prayer", "Prayer", "pr-reviewer"),
    Stat("fletching", "Fletching", "context-auditor"),
    Stat("crafting", "Crafting", "design-auditor"),
    Stat("hitpoints", "Hitpoints", "kintsugi"),
    Stat("woodcutting", "Woodcutting", "gardener"),
    Stat("thieving", "Thieving", "interrogator"),
    Stat("fishing", "Fishing", "why"),
    Stat("agility", "Agility", "how"),
    Stat("ranged", "Ranged", "blast-radius"),
    Stat("attack", "Attack", "comment-sicko"),
    Stat("slayer", "Slayer", INSTANCE),
    Stat("mining", "Mining", INSTANCE),
    Stat("farming", "Farming", INSTANCE),
)

_STAT_OF_AGENT = {s.trained_by: s.id for s in STATS if s.trained_by != INSTANCE}

#: A custom agent's skill: it has no built-in to inherit one from.
CUSTOM_STAT = "magic"

#: Experience for one successful run of an agent whose events are its runs.
RUN_XP: dict[str, int] = {
    "review": 30_000,
    "triage": 2_000,
    "analysis": 2_500,
    "research": 40_000,
    "onboarding": 150_000,
    "ops": 40_000,
    "pr-reviewer": 30_000,
    "design-auditor": 40_000,
    "kintsugi": 40_000,
    "gardener": 40_000,
    "interrogator": 40_000,
    "why": 40_000,
    "how": 40_000,
    "blast-radius": 40_000,
    "comment-sicko": 40_000,
}

#: A custom agent's run: the rate of the skill it trains.
CUSTOM_RUN_XP = RUN_XP["analysis"]

#: Experience for the events that are an outcome rather than a run.
OUTCOME_XP: dict[str, int] = {
    "merged": 90_000,
    "qa_passed": 60_000,
    "audited": 40_000,
    "healed": 120_000,
    "watched": 3_000,
    "scheduled": 4_000,
}

#: A context audit's bonus per outdated claim its report counted.
XP_PER_STALE_CLAIM = 1_000

_INSTANCE_STAT = {"healed": "slayer", "watched": "mining", "scheduled": "farming"}


def stat_of_agent(agent_type: str) -> str:
    return _STAT_OF_AGENT.get(agent_type, CUSTOM_STAT)


def price(event: ProgressEvent) -> tuple[str, str | None, int] | None:
    """The skill an event trains, the agent it credits (None for the instance's), and its experience."""
    if event.source == "run":
        if not event.agent_type:
            return None
        return stat_of_agent(event.agent_type), event.agent_type, RUN_XP.get(event.agent_type, CUSTOM_RUN_XP)
    xp = OUTCOME_XP.get(event.source)
    if xp is None:
        return None
    if event.source == "audited":
        stale, _files = report_counts(event.report)
        xp += XP_PER_STALE_CLAIM * (stale or 0)
    if event.source in _INSTANCE_STAT:
        return _INSTANCE_STAT[event.source], None, xp
    agent = event.agent_type or ""
    return stat_of_agent(agent), agent, xp


def _iso(value: datetime) -> str:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


@dataclass(frozen=True)
class LevelUp:
    at: datetime
    stat: str
    level: int
    #: The agent whose work crossed the level, None for an instance skill.
    agent_type: str | None


@dataclass
class Progression:
    stat_xp: dict[str, int]
    agent_xp: dict[str, int]
    level_ups: list[LevelUp] = field(default_factory=list)
    computed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def total_level(self) -> int:
        return sum(level_for_xp(self.stat_xp.get(s.id, 0)) for s in STATS)

    @property
    def total_xp(self) -> int:
        return sum(self.stat_xp.values())

    def to_dict(self, *, recent: int = 20) -> dict[str, Any]:
        def progress(xp: int) -> dict[str, Any]:
            level = level_for_xp(xp)
            return {
                "xp": xp,
                "level": level,
                "xp_to_next": None if level >= MAX_LEVEL else xp_for_level(level + 1) - xp,
            }

        return {
            "total_level": self.total_level,
            "total_xp": self.total_xp,
            "stats": [
                {"stat": s.id, "name": s.name, "trained_by": s.trained_by, **progress(self.stat_xp.get(s.id, 0))}
                for s in STATS
            ],
            "agents": [
                {
                    "agent_type": agent,
                    "stat": stat_of_agent(agent),
                    **progress(xp),
                    "tier": tier_for_level(level_for_xp(xp)),
                }
                for agent, xp in sorted(self.agent_xp.items())
            ],
            "recent": [
                {"kind": "level", "at": _iso(u.at), "stat": u.stat, "level": u.level, "agent_type": u.agent_type}
                for u in reversed(self.level_ups[-recent:])
            ],
            "computed_at": _iso(self.computed_at),
        }


def compute(events: Iterable[ProgressEvent], agent_types: Iterable[str] = ()) -> Progression:
    """Price every event in time order, and record each level a skill crossed.

    Every agent in ``agent_types`` is listed, at level 1 when it earned
    nothing; an agent that earned something but is no longer defined is
    listed too, since its work happened.
    """
    stat_xp = {s.id: 0 for s in STATS}
    agent_xp = dict.fromkeys(agent_types, 0)
    level_ups: list[LevelUp] = []
    for event in sorted(events, key=lambda e: e.at):
        priced = price(event)
        if priced is None:
            continue
        stat, agent, xp = priced
        before = level_for_xp(stat_xp[stat])
        stat_xp[stat] += xp
        after = level_for_xp(stat_xp[stat])
        if agent is not None:
            agent_xp[agent] = agent_xp.get(agent, 0) + xp
        if after > before:
            level_ups.append(LevelUp(event.at, stat, after, agent))
    return Progression(stat_xp, agent_xp, level_ups)


class _Repository(Protocol):
    async def events(self) -> list[ProgressEvent]: ...

    async def agent_types(self) -> list[str]: ...


class ProgressionService:
    """Computes progression from the repository, at most once per ``ttl_seconds``."""

    def __init__(
        self, repository: _Repository, *, ttl_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._repository = repository
        self._ttl = ttl_seconds
        self._clock = clock
        self._cached: tuple[float, Progression] | None = None
        self._lock = asyncio.Lock()

    async def read(self) -> Progression:
        async with self._lock:
            now = self._clock()
            if self._cached is not None and now - self._cached[0] < self._ttl:
                return self._cached[1]
            progression = compute(await self._repository.events(), await self._repository.agent_types())
            self._cached = (now, progression)
            return progression
