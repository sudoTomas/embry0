"""Progression (spec 2026-10-24): the work embry0 records, as experience and levels.

Every agent is a character with the active content pack's skills (#176),
each levelled on the pack's curve by what that agent does: every tool call
of a run that ended without an error earns a little experience in the skill
its category trains (``embry0.execution.skill_actions``), and an outcome (a
merge, a QA pass, a finished review) earns a lot. The instance is the
player's account: its experience is its characters' added up, and the work
no character does counts toward its achievements only. Nothing is stored:
the levels are recomputed from the rows under the pack's :class:`Rules`, so
the same history always reads the same way under the same pack, and a
changed pack re-prices all of it.

In code a skill is a ``stat``: embry0's Skills page already means agent
skill packs.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

from embry0.execution.skill_actions import actions_from_tools, current_actions
from embry0.services.context_audit import report_counts
from embry0.services.progression_pack import PackSource, Rules, rules_for
from embry0.services.quests import Reward, evaluate_diaries, evaluate_quests, quest_rewards
from embry0.storage.repositories.progression import Fact, FactFragment, Journal, ProgressEvent

logger = structlog.get_logger(__name__)


def run_actions(event: ProgressEvent) -> dict[str, int]:
    """A run's tool calls as today's action keys: recorded (older keys translated), else read from tool names."""
    if event.skill_actions:
        return current_actions(event.skill_actions)
    return actions_from_tools(event.tools_called or {})


def price(event: ProgressEvent, *, rules: Rules) -> list[tuple[str, int]]:
    """What an event earns its character under ``rules``: (skill, XP) pairs, none for work no character does."""
    if not event.agent_type:
        return []
    if event.source == "run":
        gains: dict[str, int] = {}
        for key, calls in run_actions(event).items():
            kind = key.partition(":")[0]
            stat = rules.skill_for_action.get(key)
            if stat is not None and kind in rules.action_xp and calls > 0:
                gains[stat] = gains.get(stat, 0) + rules.action_xp[kind] * calls
        outcome = rules.run_outcomes.get(event.agent_type)
        if outcome is not None:
            gains[outcome[0]] = gains.get(outcome[0], 0) + outcome[1]
        return list(gains.items())
    rule = rules.outcomes.get(event.source)
    if rule is None:
        return []
    xp = rule.xp
    if event.source == "cleaned":
        # Slop dungeon: a merged cleanup earns per net line it removed (its report holds the counts).
        try:
            counts = json.loads(event.report or "{}")
        except ValueError:
            counts = {}
        counts = counts if isinstance(counts, dict) else {}
        net = max(0, int(counts.get("removed") or 0) - int(counts.get("added") or 0))
        xp += rule.per_line_removed * min(net, rule.max_lines)
    elif event.source == "audited":
        stale, _files = report_counts(event.report)
        xp += rule.per_stale_claim * (stale or 0)
    return [(rule.skill, xp)]


def _iso(value: datetime) -> str:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


def _progress(xp: int, rules: Rules) -> dict[str, Any]:
    level = rules.level_for_xp(xp)
    return {
        "xp": xp,
        "level": level,
        # Where this level starts, so a progress bar needs no copy of the curve.
        "level_xp": rules.xp_for_level(level),
        "xp_to_next": None if level >= rules.max_level else rules.xp_for_level(level + 1) - xp,
    }


@dataclass(frozen=True)
class LevelUp:
    at: datetime
    agent_type: str
    stat: str
    level: int


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
    rules: Rules = field(kw_only=True, repr=False)

    def level(self, stat: str) -> int:
        return self.rules.level_for_xp(self.stat_xp.get(stat, 0))

    @property
    def xp(self) -> int:
        return sum(self.stat_xp.values())

    @property
    def total_level(self) -> int:
        return sum(self.level(s) for s in self.rules.skill_ids)

    @property
    def agent_level(self) -> int:
        """Its one level, from its five best skills (``Rules.character_level``)."""
        return self.rules.character_level([self.level(s) for s in self.rules.skill_ids])

    @property
    def best_skill(self) -> str:
        """Its highest level, the most experience breaking a tie, the pack's skill order a full tie."""
        order = self.rules.skill_ids
        best = max(range(len(order)), key=lambda i: (self.level(order[i]), self.stat_xp.get(order[i], 0), -i))
        return order[best]

    @property
    def tier(self) -> str:
        return self.rules.tier_for_level(self.level(self.best_skill)).id

    @property
    def capes(self) -> list[str]:
        """Its skills at the top level, the most experience first: the first is the cloak it wears."""
        maxed = [s for s in self.rules.skill_ids if self.level(s) >= self.rules.max_level]
        return sorted(maxed, key=lambda stat: -self.stat_xp.get(stat, 0))

    def to_dict(self, *, quest_cloak: bool = False) -> dict[str, Any]:
        tier = self.rules.tier_for_level(self.level(self.best_skill))
        return {
            "agent_type": self.agent_type,
            "level": self.agent_level,
            "xp": self.xp,
            "total_level": self.total_level,
            "best_skill": self.best_skill,
            "tier": tier.id,
            # The gear art its tier wears.
            "gear": tier.gear,
            "capes": self.capes,
            "quest_cloak": quest_cloak,
            "rares": [{"id": rare, "at": _iso(at)} for rare, at in sorted(self.rares.items(), key=lambda r: r[1])],
            "skills": [{"stat": s, **_progress(self.stat_xp.get(s, 0), self.rules)} for s in self.rules.skill_ids],
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
    #: The pack it was computed under.
    rules: Rules = field(kw_only=True, repr=False)

    @property
    def quest_points(self) -> int:
        return sum(q.points for q in self.quests if q.completed_at)

    @property
    def cloak_trim(self) -> bool:
        """A completed quest that trims every cloak (the shipped pack's capstone)."""
        return any(q.cloak_trim and q.completed_at for q in self.quests)

    @property
    def quest_cloak_owner(self) -> str | None:
        """Every quest done: the quest cloak, worn by the highest total level."""
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
        view = self.rules.view()
        return {
            "account": {
                "xp": self.xp,
                "total_level": self.total_level,
                "characters": len(self.characters),
                "quest_points": self.quest_points,
                "cloak_trim": self.cloak_trim,
                # The same, under its name before #176; kept for one release.
                "divine_trim": self.cloak_trim,
            },
            "characters": [
                self.characters[a].to_dict(quest_cloak=a == self.quest_cloak_owner) for a in sorted(self.characters)
            ],
            "achievements": [a.to_dict() for a in self.achievements],
            "quests": [q.to_dict() for q in self.quests],
            # A repo whose diary has no task done yet is left out: every repo a job ever touched has one.
            "diaries": [d.to_dict() for d in self.diaries if d.started],
            "recent": self._recent(recent),
            "computed_at": _iso(self.computed_at),
            # The pack's names, colours, words and art: the dashboard keeps no copy of them.
            **view,
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


def compute(
    events: Iterable[ProgressEvent],
    agent_types: Iterable[str] = (),
    rewards: Iterable[tuple[datetime, Reward]] = (),
    *,
    rules: Rules,
) -> Progression:
    """Price every event in time order under ``rules``, and record each level a character's skill crossed and each rare it earned.

    Every agent in ``agent_types`` is a character, at level 1 in every skill
    when it earned nothing; an agent that earned something but is no longer
    defined is one too, since its work happened. A merge on a day the pack
    gives a rare earns it, and the first run of a new year earns the pack's
    new-year rare, both on the pack's calendar. A character holds each rare
    once. ``rewards`` are completed quests' XP, each added at its quest's
    completion.
    """
    characters = {agent: Character(agent, rules=rules) for agent in agent_types}
    level_ups: list[LevelUp] = []
    agent_level_ups: list[AgentLevelUp] = []
    agent_levels: dict[str, int] = {}
    drops: list[RareDrop] = []
    last_run_year: int | None = None

    def character(agent: str) -> Character:
        found = characters.get(agent)
        if found is None:
            found = characters[agent] = Character(agent, rules=rules)
        return found

    def earn(agent: str, rare: str, at: datetime) -> None:
        holder = character(agent)
        if rare not in holder.rares:
            holder.rares[rare] = at
            drops.append(RareDrop(at, agent, rare))

    def gain(agent: str, stat: str, xp: int, at: datetime) -> None:
        holder = character(agent)
        before = holder.level(stat)
        holder.stat_xp[stat] = holder.stat_xp.get(stat, 0) + xp
        after = holder.level(stat)
        if after > before:
            level_ups.append(LevelUp(at, agent, stat, after))
            # A skill's level moved, so the agent's own level may have too.
            reached = holder.agent_level
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
            day = rules.local(event.at)
            rare = rules.rare_on_date.get((day.month, day.day))
            if rare is not None:
                earn(event.agent_type, rare, event.at)
        if event.agent_type and event.source == "run":
            year = rules.local(event.at).year
            if last_run_year is not None and year > last_run_year and rules.rare_first_run_of_year is not None:
                earn(event.agent_type, rules.rare_first_run_of_year, event.at)
            last_run_year = year
        if not event.agent_type:
            continue
        for stat, xp in price(event, rules=rules):
            gain(event.agent_type, stat, xp, event.at)
    return Progression(characters, level_ups, drops, agent_level_ups, rules=rules)


def first_total(level_ups: Iterable[LevelUp], total: int, *, rules: Rules) -> datetime | None:
    """When a character's total level first reached ``total``, or None."""
    levels: dict[tuple[str, str], int] = {}
    totals: dict[str, int] = {}
    for up in sorted(level_ups, key=lambda u: u.at):
        key = (up.agent_type, up.stat)
        totals[up.agent_type] = totals.get(up.agent_type, len(rules.skill_ids)) + up.level - levels.get(key, 1)
        levels[key] = up.level
        if totals[up.agent_type] >= total:
            return up.at
    return None


class _Repository(Protocol):
    async def facts(self, queries: Iterable[FactFragment], timezone: str) -> list[Fact]: ...

    async def journal(self) -> Journal: ...

    async def events(self) -> list[ProgressEvent]: ...

    async def agent_types(self) -> list[str]: ...


class ProgressionService:
    """Computes progression from the repository under the active pack, at most once per ``ttl_seconds``.

    ``packs`` answers the active pack. A changed pack (another hash) is
    recomputed at once, whatever the time to live.
    """

    def __init__(
        self,
        repository: _Repository,
        *,
        packs: PackSource,
        ttl_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repository = repository
        self._packs = packs
        self._ttl = ttl_seconds
        self._clock = clock
        self._cached: tuple[float, Progression] | None = None
        self._lock = asyncio.Lock()

    async def rules(self) -> Rules:
        """The active pack's rules."""
        return rules_for(await self._packs.active())

    async def _facts(self, rules: Rules) -> list[Fact]:
        """The achievements' facts on the pack's calendar; on UTC's, with a warning, when the database refuses it.

        The pack's timezone is checked against Python's timezone database,
        and Postgres applies it from its own: a name one knows and the other
        does not would fail the whole query, and with it every read of the
        progression. A failure under UTC is a real one and is raised.
        """
        timezone = rules.pack.timezone
        try:
            return await self._repository.facts(rules.fact_queries, timezone)
        except Exception:
            if timezone == "UTC":
                raise
            logger.warning("progression_facts_timezone_fallback", timezone=timezone, exc_info=True)
            return await self._repository.facts(rules.fact_queries, "UTC")

    async def read(self) -> Progression:
        async with self._lock:
            rules = await self.rules()
            now = self._clock()
            cached = self._cached
            if cached is not None and now - cached[0] < self._ttl and cached[1].rules.hash == rules.hash:
                return cached[1]
            from embry0.services.achievements import evaluate

            events = await self._repository.events()
            journal = await self._repository.journal()
            # Quest XP feeds the levels, so the quests that give it are read first.
            progression = compute(
                events, await self._repository.agent_types(), quest_rewards(journal, rules=rules), rules=rules
            )
            progression.achievements, progression.unlocks = evaluate(
                await self._facts(rules),
                progression.level_ups,
                progression.agent_level_ups,
                progression.drops,
                rules=rules,
            )
            progression.diaries = evaluate_diaries(journal, rules=rules)
            progression.quests = evaluate_quests(
                journal,
                progression.diaries,
                lambda total: first_total(progression.level_ups, total, rules=rules),
                rules=rules,
            )
            self._cached = (now, progression)
            return progression
