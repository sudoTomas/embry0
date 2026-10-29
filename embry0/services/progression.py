"""Progression (spec 2026-10-24): the work embry0 records, as experience and levels.

Every agent is a character with the active content pack's skills (#176),
each levelled on the pack's curve by what that agent does: every tool call
of a run that ended without an error earns a little experience in the skill
its category trains (``embry0.execution.skill_actions``), and an outcome (a
merge, a QA pass, a finished review) earns a lot. The instance is the
player's account: its experience is its characters' added up, and the work
no character does counts toward its achievements only. The levels are
recomputed from the rows under the pack's :class:`Rules`, so the same
history always reads the same way under the same pack, and a changed pack
re-prices all of it. Only the days that can no longer change are folded
once and kept, per pack (``progression_rollup``); a read resumes that fold,
and answers what one fold over everything answers.

In code a skill is a ``stat``: embry0's Skills page already means agent
skill packs.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Protocol

import asyncpg
import structlog

from embry0.execution.skill_actions import actions_from_tools, current_actions
from embry0.services.context_audit import report_counts
from embry0.services.progression_pack import PackSource, Rules, rules_for
from embry0.services.quests import Reward, evaluate_diaries, evaluate_quests, quest_rewards, run_agents
from embry0.storage.repositories.progression import Fact, FactFragment, Journal, ProgressEvent

if TYPE_CHECKING:
    from embry0.services.progression_rollup import Maintained, RollupStore

logger = structlog.get_logger(__name__)

#: A rollup write that failed is retried after this many seconds, then twice as long each time, up to the maximum.
WRITE_RETRY_FIRST = 60.0
WRITE_RETRY_MAX = 3600.0
#: How long shutdown waits for a background rollup write to stop.
SHUTDOWN_WAIT = 10.0


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
    #: The calendar the achievements' facts were read on: the pack's timezone, or UTC when Postgres refused it.
    timezone_applied: str | None = None
    #: The pack it was computed under.
    rules: Rules = field(kw_only=True, repr=False)
    #: The rollup its experience was read from (``progression_rollup.view``); None when from every raw row.
    rollup: dict[str, Any] | None = None

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
        # The calendar the facts were read on; another than the pack's means Postgres refused the pack's.
        view["pack"] = {**view["pack"], "timezone_applied": self.timezone_applied or self.rules.pack.timezone}
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
            # The closed days the experience was folded from once; an event recorded late for one counts from the
            # next full rebuild (docs/progression.md). None when every raw row was read.
            "rollup": self.rollup,
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


class Fold:
    """:func:`compute`'s loop, which can stop at an instant and go on from what it carried.

    Everything is priced in time order: an event's XP goes to its
    character's skill, and each level a skill crosses, each character level
    reached and each rare earned is recorded at the event's time. What a fold
    carries from one event to the next is its characters (their XP and
    rares), the character levels reached and the year of the last run; the
    rollup (``embry0/services/progression_rollup.py``) stores exactly that,
    so a fold resumed from it after an instant and fed the events after it
    ends where one fold over everything ends.

    ``daily``, when not None, adds up the XP gained per day of the pack's
    calendar, character and skill: the rows of the rollup.
    """

    def __init__(self, *, rules: Rules, agent_types: Iterable[str] = ()) -> None:
        self.rules = rules
        self.characters: dict[str, Character] = {agent: Character(agent, rules=rules) for agent in agent_types}
        self.level_ups: list[LevelUp] = []
        self.agent_level_ups: list[AgentLevelUp] = []
        self.drops: list[RareDrop] = []
        #: Each character's own level, once a skill's level-up moved it above 1.
        self.agent_levels: dict[str, int] = {}
        #: The pack-calendar year of the last run folded, for the new-year rare.
        self.last_run_year: int | None = None
        self.daily: dict[tuple[date, str, str], int] | None = None

    def character(self, agent: str) -> Character:
        found = self.characters.get(agent)
        if found is None:
            found = self.characters[agent] = Character(agent, rules=self.rules)
        return found

    def _earn(self, agent: str, rare: str, at: datetime) -> None:
        holder = self.character(agent)
        if rare not in holder.rares:
            holder.rares[rare] = at
            self.drops.append(RareDrop(at, agent, rare))

    def _gain(self, agent: str, stat: str, xp: int, at: datetime) -> None:
        holder = self.character(agent)
        before = holder.level(stat)
        holder.stat_xp[stat] = holder.stat_xp.get(stat, 0) + xp
        if self.daily is not None:
            key = (self.rules.local(at).date(), agent, stat)
            self.daily[key] = self.daily.get(key, 0) + xp
        after = holder.level(stat)
        if after > before:
            self.level_ups.append(LevelUp(at, agent, stat, after))
            # A skill's level moved, so the agent's own level may have too.
            reached = holder.agent_level
            if reached > self.agent_levels.get(agent, 1):
                self.agent_levels[agent] = reached
                self.agent_level_ups.append(AgentLevelUp(at, agent, reached))

    def feed(self, events: Iterable[ProgressEvent], rewards: Iterable[tuple[datetime, Reward]] = ()) -> None:
        """Fold ``events`` and quest ``rewards`` in time order; at one time, the events first, in their order.

        Everything fed must be later than everything fed before.
        """
        rules = self.rules
        timeline: list[tuple[datetime, ProgressEvent | Reward]] = [(e.at, e) for e in events]
        timeline += list(rewards)
        for at, item in sorted(timeline, key=lambda pair: pair[0]):
            if isinstance(item, Reward):
                self._gain(item.agent_type, item.stat, item.xp, at)
                continue
            event = item
            if event.agent_type and event.source == "merged":
                day = rules.local(event.at)
                rare = rules.rare_on_date.get((day.month, day.day))
                if rare is not None:
                    self._earn(event.agent_type, rare, event.at)
            if event.agent_type and event.source == "run":
                year = rules.local(event.at).year
                if (
                    self.last_run_year is not None
                    and year > self.last_run_year
                    and rules.rare_first_run_of_year is not None
                ):
                    self._earn(event.agent_type, rules.rare_first_run_of_year, event.at)
                self.last_run_year = year
            if not event.agent_type:
                continue
            for stat, xp in price(event, rules=rules):
                self._gain(event.agent_type, stat, xp, event.at)

    def progression(self) -> Progression:
        return Progression(self.characters, self.level_ups, self.drops, self.agent_level_ups, rules=self.rules)


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
    fold = Fold(rules=rules, agent_types=agent_types)
    fold.feed(events, rewards)
    return fold.progression()


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


def pack_changes(before: Progression, after: Progression) -> dict[str, Any]:
    """What another pack would change, for its preview (#176): before and after, under each pack.

    Each character's level, total level and experience (null on the side
    where it is not a character), the account's totals, and how many
    achievements have at least one tier unlocked.
    """

    def character(progression: Progression, agent: str) -> dict[str, int] | None:
        found = progression.characters.get(agent)
        if found is None:
            return None
        return {"level": found.agent_level, "total_level": found.total_level, "xp": found.xp}

    def unlocked(progression: Progression) -> int:
        return sum(1 for state in progression.achievements if state.tier > 0)

    agents = sorted(set(before.characters) | set(after.characters))
    return {
        "characters": [
            {"agent_type": agent, "before": character(before, agent), "after": character(after, agent)}
            for agent in agents
        ],
        "account": {
            "before": {"total_level": before.total_level, "xp": before.xp},
            "after": {"total_level": after.total_level, "xp": after.xp},
        },
        "achievements_unlocked": {"before": unlocked(before), "after": unlocked(after)},
    }


class _Repository(Protocol):
    async def facts(self, queries: Iterable[FactFragment], timezone: str) -> list[Fact]: ...

    async def journal(self, agents: Collection[str] | None = None) -> Journal: ...

    async def events(self, *, since: datetime | None = None, until: datetime | None = None) -> list[ProgressEvent]: ...

    async def agent_types(self) -> list[str]: ...


class ProgressionService:
    """Computes progression from the repository under the active pack, at most once per ``ttl_seconds``.

    ``packs`` answers the active pack. A changed pack (another hash) is
    recomputed at once, whatever the time to live.

    ``rollups`` keeps the closed days folded (``progression_rollup``), so a
    read folds only the raw events of the last few days; None folds every
    raw event on every read. Production passes the rollup tables. The
    rollup is written in the background, never on a read; ``now`` is the
    wall clock that decides which days are closed.

    Every write of this process, a background one or an admin's rebuild,
    goes through one lock, so at most one of its pool connections waits on
    the rollup's advisory lock while the writer holding it needs another. A
    write that fails is retried after a delay that doubles, from
    :data:`WRITE_RETRY_FIRST` to :data:`WRITE_RETRY_MAX`, not on every read.
    """

    def __init__(
        self,
        repository: _Repository,
        *,
        packs: PackSource,
        rollups: RollupStore | None,
        ttl_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        from embry0.services.progression_rollup import ProgressionRollup

        self._repository = repository
        self._packs = packs
        self._rollup = None if rollups is None else ProgressionRollup(rollups, repository, now=now)
        self._ttl = ttl_seconds
        self._clock = clock
        self._cached: tuple[float, Progression] | None = None
        self._lock = asyncio.Lock()
        #: The background write, and the lock every write of this process takes.
        self._maintenance: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._write_failures = 0
        self._write_retry_at = 0.0
        #: Moves with every rollup write: a read that began before one does not cache what it computed.
        self._generation = 0

    async def rules(self) -> Rules:
        """The active pack's rules."""
        return rules_for(await self._packs.active())

    async def _facts(self, rules: Rules) -> tuple[list[Fact], str]:
        """The achievements' facts and the calendar they were read on: the pack's, or UTC's when Postgres refuses it.

        The pack's timezone is checked against Python's timezone database,
        and Postgres applies it from its own: a name one knows and the other
        does not would fail the whole query, and with it every read of the
        progression. A write checks the name against Postgres too (#176),
        so this covers a pack stored before that check, or a database whose
        tz data changed since. Only Postgres's refusal of the time zone
        falls back; any other failure (a statement timeout, a dropped
        connection) is raised, since a retry on UTC would count the
        oddities on the wrong days for the next minute without a word.
        """
        timezone = rules.pack.timezone
        try:
            return await self._repository.facts(rules.fact_queries, timezone), timezone
        except asyncpg.InvalidParameterValueError as exc:
            if timezone == "UTC" or "time zone" not in str(exc):
                raise
            logger.warning("progression_facts_timezone_fallback", timezone=timezone, applied="UTC")
            return await self._repository.facts(rules.fact_queries, "UTC"), "UTC"

    async def read(self) -> Progression:
        async with self._lock:
            rules = await self.rules()
            now = self._clock()
            cached = self._cached
            if cached is not None and now - cached[0] < self._ttl and cached[1].rules.hash == rules.hash:
                return cached[1]
            generation = self._generation
            progression = await self._progression(rules, through_rollup=True)
            if generation == self._generation:
                # A write that finished meanwhile (one this read may have started) makes it stale at once.
                self._cached = (now, progression)
            return progression

    async def compute(self, rules: Rules) -> Progression:
        """The progression under ``rules``, computed now from the rows: never cached, and the cache left as it was.

        What a draft pack's preview reads (#176): the same history, priced
        under a pack nobody sees yet. It folds every raw event and never
        reads or writes a rollup, which belongs to the active pack.
        """
        return await self._progression(rules, through_rollup=False)

    async def _progression(self, rules: Rules, *, through_rollup: bool) -> Progression:
        from embry0.services.achievements import evaluate

        journal = await self._repository.journal(run_agents(rules))
        # Quest XP feeds the levels, so the quests that give it are read first.
        rewards = quest_rewards(journal, rules=rules)
        agent_types = await self._repository.agent_types()
        if through_rollup:
            progression = await self._levels(rules, agent_types, rewards)
        else:
            progression = compute(await self._repository.events(), agent_types, rewards, rules=rules)
        facts, progression.timezone_applied = await self._facts(rules)
        progression.achievements, progression.unlocks = evaluate(
            facts,
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
        return progression

    async def _levels(
        self, rules: Rules, agent_types: list[str], rewards: list[tuple[datetime, Reward]]
    ) -> Progression:
        """The levels: from the rollup and the recent raw events, else from every raw event."""
        if self._rollup is not None:
            levels = await self._rollup.levels(rules, agent_types, rewards)
            if levels.due:
                self._schedule(rules)
            if levels.progression is not None:
                return levels.progression
        return compute(await self._repository.events(), agent_types, rewards, rules=rules)

    def _schedule(self, rules: Rules) -> None:
        """Write the rollup in the background, unless a write is running or a failed one is waiting to retry."""
        if self._maintenance is not None and not self._maintenance.done():
            return
        if self._clock() < self._write_retry_at:
            return
        self._maintenance = asyncio.get_running_loop().create_task(self._background_write(rules))

    async def _background_write(self, rules: Rules) -> None:
        try:
            await self._write(rules)
        except Exception:
            # The reads go on from the rollup as it stands, or from the raw rows, and the write waits to retry.
            self._write_failures += 1
            delay = min(WRITE_RETRY_FIRST * 2 ** (self._write_failures - 1), WRITE_RETRY_MAX)
            self._write_retry_at = self._clock() + delay
            logger.exception("progression_rollup_failed", pack_hash=rules.hash, retry_in_seconds=delay)

    async def _write(self, rules: Rules, *, force_since: datetime | None = None) -> Maintained:
        """One write of the rollup, after any other write of this process."""
        if self._rollup is None:
            raise RuntimeError("this progression service keeps no rollup")
        async with self._write_lock:
            done = await self._rollup.maintain(rules, force_since=force_since)
        self._write_failures, self._write_retry_at = 0, 0.0
        if done.action is not None:
            self._generation += 1
            self._cached = None
            logger.info(
                "progression_rollup_written",
                action=done.action,
                pack_hash=rules.hash,
                through_day=done.head.through_day.isoformat(),
            )
        return done

    async def settle(self) -> None:
        """Wait for the background rollup write this process started, if one is running."""
        task = self._maintenance
        if task is not None:
            await task

    async def aclose(self) -> None:
        """Stop a background rollup write at shutdown: its transaction rolls back, and nothing is half written.

        Waits at most :data:`SHUTDOWN_WAIT` seconds for it to stop, like the lifespan's other tasks.
        """
        task = self._maintenance
        if task is None or task.done():
            return
        task.cancel()
        _done, pending = await asyncio.wait({task}, timeout=SHUTDOWN_WAIT)
        if pending:
            logger.warning("progression_rollup_shutdown_timeout", timeout_seconds=SHUTDOWN_WAIT)

    async def rebuild(self) -> dict[str, Any]:
        """Fold every closed day again now and wait for it (``POST /progression/rebuild``).

        Reads go on meanwhile, from the rollup as it stands. The call waits
        for a write of this process already running, then rebuilds. A full
        rebuild that started after the call arrived, here or in another
        orchestrator, counts as this one; one that was already running when
        it arrived does not.
        """
        if self._rollup is None:
            raise RuntimeError("this progression service keeps no rollup")
        from embry0.services.progression_rollup import view

        requested = self._rollup.now()
        done = await self._write(await self.rules(), force_since=requested)
        return {"action": done.action, "rollup": view(done.head)}
