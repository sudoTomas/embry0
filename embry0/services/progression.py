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
import json
import math
import time
from bisect import bisect_right
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from embry0.execution.skill_actions import actions_from_tools
from embry0.services.context_audit import report_counts
from embry0.services.quests import Reward, evaluate_diaries, evaluate_quests, quest_rewards
from embry0.services.slop import cleaned_xp
from embry0.storage.repositories.progression import Fact, Journal, ProgressEvent

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

#: The total level a character needs before Divine Slayer can complete.
DIVINE_SLAYER_TOTAL = 1_000

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
    if event.source == "cleaned":
        # Slop dungeon: a merged cleanup earns per net line it removed (its report holds the counts).
        try:
            counts = json.loads(event.report or "{}")
        except ValueError:
            counts = {}
        counts = counts if isinstance(counts, dict) else {}
        return [("coding", cleaned_xp(counts.get("added"), counts.get("removed")))]
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


#: The rares (spec 2026-10-24) a merge earns on a day of the year (UTC), by (month, day).
RARE_DAYS: dict[tuple[int, int], str] = {(12, 25): "party-hat", (10, 31): "holiday-mask"}

#: The rare the first run of a new year earns.
NEW_YEAR_RARE = "white-party-hat"


@dataclass(frozen=True)
class AgentLevelUp:
    at: datetime
    agent_type: str
    level: int


@dataclass(frozen=True)
class RareDrop:
    at: datetime
    agent_type: str
    rare: str


@dataclass
class Character:
    """One agent's skills, its experience in each, and the rares its work earned."""

    agent_type: str
    stat_xp: dict[str, int] = field(default_factory=dict)
    #: Each rare it holds and when it first earned it.
    rares: dict[str, datetime] = field(default_factory=dict)

    def level(self, stat: str) -> int:
        return level_for_xp(self.stat_xp.get(stat, 0))

    @property
    def xp(self) -> int:
        return sum(self.stat_xp.values())

    @property
    def total_level(self) -> int:
        return sum(self.level(s.id) for s in STATS)

    @property
    def agent_level(self) -> int:
        """Its one level, 1 to 126: the combat-level formula with its best skills in the combat slots.

        The two best stand where Attack and Strength do, the next three where
        Defence, Hitpoints and Prayer do (Prayer counting half), so depth and
        breadth both count and every skill at 99 makes 126.
        """
        top = sorted((self.level(s.id) for s in STATS), reverse=True)
        return math.floor(0.25 * (top[2] + top[3] + top[4] // 2) + 0.325 * (top[0] + top[1]))

    @property
    def best_skill(self) -> str:
        """Its highest level, the most experience breaking a tie, the catalogue order a full tie."""
        return max(STATS, key=lambda s: (self.level(s.id), self.stat_xp.get(s.id, 0), -STATS.index(s))).id

    @property
    def tier(self) -> str:
        return tier_for_level(self.level(self.best_skill))

    @property
    def capes(self) -> list[str]:
        """Its skills at 99, the most experience first: the first is the cape it wears."""
        maxed = [s.id for s in STATS if self.level(s.id) >= MAX_LEVEL]
        return sorted(maxed, key=lambda stat: -self.stat_xp.get(stat, 0))

    def to_dict(self, *, quest_cape: bool = False) -> dict[str, Any]:
        return {
            "agent_type": self.agent_type,
            "level": self.agent_level,
            "xp": self.xp,
            "total_level": self.total_level,
            "best_skill": self.best_skill,
            "tier": self.tier,
            "capes": self.capes,
            "quest_cape": quest_cape,
            "rares": [{"id": rare, "at": _iso(at)} for rare, at in sorted(self.rares.items(), key=lambda r: r[1])],
            "skills": [{"stat": s.id, **_progress(self.stat_xp.get(s.id, 0))} for s in STATS],
        }


@dataclass
class Progression:
    characters: dict[str, Character]
    level_ups: list[LevelUp] = field(default_factory=list)
    drops: list[RareDrop] = field(default_factory=list)
    agent_level_ups: list[AgentLevelUp] = field(default_factory=list)
    computed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    #: Each achievement's state and every tier unlock (``services.achievements``), set by the service.
    achievements: list[Any] = field(default_factory=list)
    unlocks: list[Any] = field(default_factory=list)
    #: Each quest's state and every repo diary (``services.quests``), set by the service.
    quests: list[Any] = field(default_factory=list)
    diaries: list[Any] = field(default_factory=list)

    @property
    def quest_points(self) -> int:
        return sum(q.points for q in self.quests if q.completed_at)

    @property
    def divine_trim(self) -> bool:
        """Divine Slayer's reward: a Divine trim on every cape."""
        return any(q.id == "divine_slayer" and q.completed_at for q in self.quests)

    @property
    def quest_cape_owner(self) -> str | None:
        """Every quest done: the quest cape, worn by the highest total level."""
        if not self.quests or not all(q.completed_at for q in self.quests) or not self.characters:
            return None
        return min(self.characters.values(), key=lambda c: (-c.total_level, c.agent_type)).agent_type

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
                "quest_points": self.quest_points,
                "divine_trim": self.divine_trim,
            },
            "skills": [{"id": s.id, "name": s.name, "group": s.group, "echo": s.echo} for s in STATS],
            "characters": [
                self.characters[a].to_dict(quest_cape=a == self.quest_cape_owner) for a in sorted(self.characters)
            ],
            "achievements": [a.to_dict() for a in self.achievements],
            "achievement_categories": _achievement_categories(),
            "quests": [q.to_dict() for q in self.quests],
            # A repo whose diary has no task done yet is left out: every repo a job ever touched has one.
            "diaries": [d.to_dict() for d in self.diaries if d.started],
            "recent": self._recent(recent),
            "computed_at": _iso(self.computed_at),
        }

    def _recent(self, count: int) -> list[dict[str, Any]]:
        """The latest levels reached, rares dropped, achievement tiers unlocked, quests and diary tiers completed, newest first."""
        levels = [
            (u.at, {"kind": "level", "at": _iso(u.at), "agent_type": u.agent_type, "stat": u.stat, "level": u.level})
            for u in self.level_ups
        ]
        drops = [
            (d.at, {"kind": "rare", "at": _iso(d.at), "agent_type": d.agent_type, "id": d.rare}) for d in self.drops
        ]
        agent_levels = [
            (u.at, {"kind": "agent_level", "at": _iso(u.at), "agent_type": u.agent_type, "level": u.level})
            for u in self.agent_level_ups
        ]
        unlocks = [(u.at, u.to_dict()) for u in self.unlocks]
        quests = [
            (q.completed_at, {"kind": "quest", "at": _iso(q.completed_at), "id": q.id, "name": q.name})
            for q in self.quests
            if q.completed_at
        ]
        diaries = [
            (t.completed_at, {"kind": "diary", "at": _iso(t.completed_at), "repo": d.repo, "tier": t.tier})
            for d in self.diaries
            for t in d.tiers
            if t.completed_at
        ]
        merged = sorted(
            levels + agent_levels + drops + unlocks + quests + diaries, key=lambda item: item[0], reverse=True
        )
        return [entry for _at, entry in merged[:count]]


def _achievement_categories() -> list[dict[str, str]]:
    """The achievement diary's categories, in the order the page shows them."""
    from embry0.services.achievements import CATEGORIES

    return [{"id": c.id, "name": c.name, "blurb": c.blurb} for c in CATEGORIES]


def _utc(at: datetime) -> datetime:
    return at.astimezone(UTC) if at.tzinfo else at.replace(tzinfo=UTC)


def compute(
    events: Iterable[ProgressEvent],
    agent_types: Iterable[str] = (),
    rewards: Iterable[tuple[datetime, Reward]] = (),
) -> Progression:
    """Price every event in time order, and record each level a character's skill crossed and each rare it earned.

    Every agent in ``agent_types`` is a character, at level 1 in every skill
    when it earned nothing; an agent that earned something but is no longer
    defined is one too, since its work happened. A merge on 25 December earns
    a party hat and on 31 October a holiday mask; the first run of a new year
    earns a white party hat. A character holds each rare once. ``rewards``
    are completed quests' XP, each added at its quest's completion.
    """
    characters = {agent: Character(agent) for agent in agent_types}
    level_ups: list[LevelUp] = []
    agent_level_ups: list[AgentLevelUp] = []
    agent_levels: dict[str, int] = {}
    drops: list[RareDrop] = []
    last_run_year: int | None = None

    def earn(agent: str, rare: str, at: datetime) -> None:
        character = characters.setdefault(agent, Character(agent))
        if rare not in character.rares:
            character.rares[rare] = at
            drops.append(RareDrop(at, agent, rare))

    def gain(agent: str, stat: str, xp: int, at: datetime) -> None:
        character = characters.setdefault(agent, Character(agent))
        before = character.level(stat)
        character.stat_xp[stat] = character.stat_xp.get(stat, 0) + xp
        after = character.level(stat)
        if after > before:
            level_ups.append(LevelUp(at, agent, stat, after))
            # A skill's level moved, so the agent's own level may have too.
            reached = character.agent_level
            if reached > agent_levels.get(agent, 1):
                agent_levels[agent] = reached
                agent_level_ups.append(AgentLevelUp(at, agent, reached))

    timeline: list[tuple[datetime, ProgressEvent | Reward]] = [(e.at, e) for e in events]
    timeline += list(rewards)
    for at, item in sorted(timeline, key=lambda pair: pair[0]):
        if isinstance(item, Reward):
            gain(item.agent_type, item.stat, item.xp, at)
            continue
        event = item
        if event.agent_type and event.source == "merged":
            day = _utc(event.at)
            if (day.month, day.day) in RARE_DAYS:
                earn(event.agent_type, RARE_DAYS[(day.month, day.day)], event.at)
        if event.agent_type and event.source == "run":
            year = _utc(event.at).year
            if last_run_year is not None and year > last_run_year:
                earn(event.agent_type, NEW_YEAR_RARE, event.at)
            last_run_year = year
        if not event.agent_type:
            continue
        for stat, xp in price(event):
            gain(event.agent_type, stat, xp, event.at)
    return Progression(characters, level_ups, drops, agent_level_ups)


def first_total(level_ups: Iterable[LevelUp], total: int) -> datetime | None:
    """When a character's total level first reached ``total``, or None."""
    levels: dict[tuple[str, str], int] = {}
    totals: dict[str, int] = {}
    for up in sorted(level_ups, key=lambda u: u.at):
        key = (up.agent_type, up.stat)
        totals[up.agent_type] = totals.get(up.agent_type, len(STATS)) + up.level - levels.get(key, 1)
        levels[key] = up.level
        if totals[up.agent_type] >= total:
            return up.at
    return None


class _Repository(Protocol):
    async def facts(self) -> list[Fact]: ...

    async def journal(self) -> Journal: ...

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
            journal = await self._repository.journal()
            # Quest XP feeds the levels, so the quests that give it are read first.
            progression = compute(events, await self._repository.agent_types(), quest_rewards(journal))
            progression.achievements, progression.unlocks = evaluate(
                await self._repository.facts(),
                progression.level_ups,
                progression.agent_level_ups,
                progression.drops,
            )
            progression.diaries = evaluate_diaries(journal)
            progression.quests = evaluate_quests(
                journal, progression.diaries, first_total(progression.level_ups, DIVINE_SLAYER_TOTAL)
            )
            self._cached = (now, progression)
            return progression
