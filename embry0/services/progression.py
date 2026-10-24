"""Progression (spec 2026-10-24): the work embry0 records, as experience and levels.

Every agent is a character with the same 23 skills, each levelled from 1 to
99 on the classic curve by what that agent does: every tool call of a run
that ended without an error earns a little experience in the skill it
trains (``embry0.execution.skill_actions``), and an outcome (a merge, a QA
pass, a finished review) earns a lot. The instance is the player's account:
its experience is its characters' added up, and the work no character does
(the watcher, the schedules) counts toward its achievements only. Nothing
is stored: the levels are recomputed from the rows, so the same history
always reads the same way.

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

from embry0.execution.skill_actions import actions_from_tools
from embry0.services.context_audit import report_counts
from embry0.storage.repositories.progression import Fact, ProgressEvent

MAX_LEVEL = 99


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


#: The gear a character wears from its best skill, highest first: (from level, tier).
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
    #: Build, Verify, Investigate or Care: where its tile sits on a skill sheet.
    group: str
    #: The classic skill it echoes, which gives it its icon and cape colours.
    echo: str


STATS: tuple[Stat, ...] = (
    Stat("coding", "Coding", "build", "smithing"),
    Stat("version_control", "Version control", "build", "woodcutting"),
    Stat("building", "Building", "build", "construction"),
    Stat("shell", "Shell", "build", "strength"),
    Stat("databases", "Databases", "build", "mining"),
    Stat("testing", "Testing", "verify", "cooking"),
    Stat("quality_assurance", "Quality assurance", "verify", "herblore"),
    Stat("browsing", "Browsing", "verify", "agility"),
    Stat("reviewing", "Reviewing", "verify", "defence"),
    Stat("linting", "Linting", "verify", "crafting"),
    Stat("security", "Security", "verify", "prayer"),
    Stat("reading", "Reading", "investigate", "fishing"),
    Stat("searching", "Searching", "investigate", "hunter"),
    Stat("debugging", "Debugging", "investigate", "slayer"),
    Stat("research", "Research", "investigate", "magic"),
    Stat("triage", "Triage", "investigate", "attack"),
    Stat("documentation", "Documentation", "care", "fletching"),
    Stat("design", "Design", "care", "runecraft"),
    Stat("operations", "Operations", "care", "firemaking"),
    Stat("planning", "Planning", "care", "farming"),
    Stat("delegation", "Delegation", "care", "thieving"),
    Stat("communication", "Communication", "care", "ranged"),
    Stat("recovery", "Recovery", "care", "hitpoints"),
)

STAT_IDS = frozenset(s.id for s in STATS)

#: Experience for one tool call, by its kind (the part of the action key before the colon).
ACTION_XP: dict[str, int] = {
    "edit": 1_400,
    "shell": 600,
    "read": 1_000,
    "search": 800,
    "browser": 1_000,
    "log": 400,
    "web": 2_000,
    "agent": 2_000,
    "plan": 2_000,
}

#: A finished run's outcome, for the agents whose run is the outcome: (skill, XP).
RUN_OUTCOME: dict[str, tuple[str, int]] = {
    "review": ("reviewing", 30_000),
    "pr-reviewer": ("reviewing", 30_000),
    "triage": ("triage", 2_000),
    "research": ("research", 40_000),
    "ops": ("operations", 40_000),
    "onboarding": ("operations", 40_000),
    "design-auditor": ("design", 40_000),
    "blast-radius": ("security", 40_000),
}

#: The outcomes that are their own event: (skill, XP).
OUTCOME_XP: dict[str, tuple[str, int]] = {
    "merged": ("coding", 60_000),
    "opened": ("version_control", 20_000),
    "qa_passed": ("quality_assurance", 50_000),
    "audited": ("documentation", 40_000),
    "asked": ("communication", 20_000),
    "comeback": ("recovery", 90_000),
    "resumed": ("recovery", 90_000),
}

#: A context audit's bonus per outdated claim its report counted.
XP_PER_STALE_CLAIM = 1_000


def run_actions(event: ProgressEvent) -> dict[str, int]:
    """A run's tool calls as action keys: recorded since ``skill_actions``, else read from tool names."""
    if event.skill_actions:
        return dict(event.skill_actions)
    return actions_from_tools(event.tools_called or {})


def price(event: ProgressEvent) -> list[tuple[str, int]]:
    """What an event earns its character: (skill, XP) pairs, none for work no character does."""
    if not event.agent_type:
        return []
    if event.source == "run":
        gains: dict[str, int] = {}
        for key, calls in run_actions(event).items():
            kind, _, stat = key.partition(":")
            if stat in STAT_IDS and kind in ACTION_XP and calls > 0:
                gains[stat] = gains.get(stat, 0) + ACTION_XP[kind] * calls
        outcome = RUN_OUTCOME.get(event.agent_type)
        if outcome is not None:
            gains[outcome[0]] = gains.get(outcome[0], 0) + outcome[1]
        return list(gains.items())
    if event.source not in OUTCOME_XP:
        return []
    stat, xp = OUTCOME_XP[event.source]
    if event.source == "audited":
        stale, _files = report_counts(event.report)
        xp += XP_PER_STALE_CLAIM * (stale or 0)
    return [(stat, xp)]


def _iso(value: datetime) -> str:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


def _progress(xp: int) -> dict[str, Any]:
    level = level_for_xp(xp)
    return {
        "xp": xp,
        "level": level,
        # Where this level starts, so a progress bar needs no copy of the curve.
        "level_xp": xp_for_level(level),
        "xp_to_next": None if level >= MAX_LEVEL else xp_for_level(level + 1) - xp,
    }


@dataclass(frozen=True)
class LevelUp:
    at: datetime
    agent_type: str
    stat: str
    level: int


@dataclass
class Character:
    """One agent's skills: its experience in each."""

    agent_type: str
    stat_xp: dict[str, int] = field(default_factory=dict)

    def level(self, stat: str) -> int:
        return level_for_xp(self.stat_xp.get(stat, 0))

    @property
    def xp(self) -> int:
        return sum(self.stat_xp.values())

    @property
    def total_level(self) -> int:
        return sum(self.level(s.id) for s in STATS)

    @property
    def best_skill(self) -> str:
        """Its highest level, the most experience breaking a tie, the catalogue order a full tie."""
        return max(STATS, key=lambda s: (self.level(s.id), self.stat_xp.get(s.id, 0), -STATS.index(s))).id

    @property
    def tier(self) -> str:
        return tier_for_level(self.level(self.best_skill))

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_type": self.agent_type,
            "xp": self.xp,
            "total_level": self.total_level,
            "best_skill": self.best_skill,
            "tier": self.tier,
            "skills": [{"stat": s.id, **_progress(self.stat_xp.get(s.id, 0))} for s in STATS],
        }


@dataclass
class Progression:
    characters: dict[str, Character]
    level_ups: list[LevelUp] = field(default_factory=list)
    computed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    #: Each achievement's state and every tier unlock (``services.achievements``), set by the service.
    achievements: list[Any] = field(default_factory=list)
    unlocks: list[Any] = field(default_factory=list)

    @property
    def xp(self) -> int:
        """The account's experience: every character's added up."""
        return sum(c.xp for c in self.characters.values())

    @property
    def total_level(self) -> int:
        """The account total: every character's total level added up."""
        return sum(c.total_level for c in self.characters.values())

    def to_dict(self, *, recent: int = 20) -> dict[str, Any]:
        return {
            "account": {
                "xp": self.xp,
                "total_level": self.total_level,
                "characters": len(self.characters),
            },
            "skills": [{"id": s.id, "name": s.name, "group": s.group, "echo": s.echo} for s in STATS],
            "characters": [self.characters[a].to_dict() for a in sorted(self.characters)],
            "achievements": [a.to_dict() for a in self.achievements],
            "recent": self._recent(recent),
            "computed_at": _iso(self.computed_at),
        }

    def _recent(self, count: int) -> list[dict[str, Any]]:
        """The latest levels reached and achievement tiers unlocked, newest first."""
        levels = [
            (u.at, {"kind": "level", "at": _iso(u.at), "agent_type": u.agent_type, "stat": u.stat, "level": u.level})
            for u in self.level_ups
        ]
        unlocks = [(u.at, u.to_dict()) for u in self.unlocks]
        merged = sorted(levels + unlocks, key=lambda item: item[0], reverse=True)
        return [entry for _at, entry in merged[:count]]


def compute(events: Iterable[ProgressEvent], agent_types: Iterable[str] = ()) -> Progression:
    """Price every event in time order, and record each level a character's skill crossed.

    Every agent in ``agent_types`` is a character, at level 1 in every skill
    when it earned nothing; an agent that earned something but is no longer
    defined is one too, since its work happened.
    """
    characters = {agent: Character(agent) for agent in agent_types}
    level_ups: list[LevelUp] = []
    for event in sorted(events, key=lambda e: e.at):
        gains = price(event)
        if not gains or not event.agent_type:
            continue
        character = characters.setdefault(event.agent_type, Character(event.agent_type))
        for stat, xp in gains:
            before = character.level(stat)
            character.stat_xp[stat] = character.stat_xp.get(stat, 0) + xp
            after = character.level(stat)
            if after > before:
                level_ups.append(LevelUp(event.at, event.agent_type, stat, after))
    return Progression(characters, level_ups)


class _Repository(Protocol):
    async def facts(self) -> list[Fact]: ...

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
            from embry0.services.achievements import evaluate

            events = await self._repository.events()
            progression = compute(events, await self._repository.agent_types())
            progression.achievements, progression.unlocks = evaluate(
                events, await self._repository.facts(), progression.level_ups
            )
            self._cached = (now, progression)
            return progression
