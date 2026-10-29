"""The progression rollup (#176, spec 2026-10-28 section 7): the closed days folded once, not on every read.

Experience is a fold over every event in time order
(:class:`~embry0.services.progression.Fold`). The days of the pack's calendar
that ended more than :data:`SETTLE` ago are folded once and stored, per pack
hash: the experience per day, character and skill, every milestone at its
exact time, and what the fold carries past the last of them. A read resumes
that fold and feeds it only the events after the boundary, the instant the
newest closed day ended, so it reads a few days of raw rows instead of all
of them, and answers exactly what one fold over everything answers.

When the stored fold is written:

- **build**: no rollup for the active pack's hash (the first read, or the
  pack changed), or one folded by another release's code
  (:data:`FOLD_VERSION`). Every other rollup is deleted with it.
- **advance**: a day closed since the last write; the days between are
  folded on top and their rows appended.
- **rebuild**: the nightly full fold, at the first read after
  :data:`NIGHTLY_AT` on the pack's calendar; a quest reward folded in that
  moved (a late merge completes a quest in the past); or an admin's
  ``POST /progression/rebuild``.

Writes happen in the background, never on a read: a read whose rollup is
missing or holds rewards that moved answers from the raw rows, as before the
rollup, and schedules the build. Writers take one advisory lock, so two
replicas build once; a reader's snapshot never sees a write half done.

What stays stale: an event recorded more than :data:`SETTLE` after its own
time, for a day already folded, counts from the next full rebuild, so at
most a day late. So does a change to a row of a folded day (a job's last
run names whom its resumed event credits). An event's time never moves (a
job's events happen when it finished, never at its update time), so none
is counted twice. Achievements, quests and diaries read the raw rows on
every read and are never stale; GET /progression reports the rollup's times.
"""

from __future__ import annotations

import hashlib
import importlib.util
from collections.abc import Callable, Collection, Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

from embry0.services.progression import AgentLevelUp, Fold, LevelUp, Progression, RareDrop
from embry0.services.progression_pack import Rules
from embry0.services.quests import Reward, quest_rewards, run_agents
from embry0.storage.repositories.progression import Journal, ProgressEvent
from embry0.storage.repositories.progression_rollup import DailyXp, Milestone, RollupHead, StoredRollup

#: A day is closed, and folded into the rollup, once it ended this long ago: a run keeps the time it
#: started and can finish much later, and a merge arrives with the forge's time.
SETTLE = timedelta(hours=48)
#: The nightly full rebuild runs at the first read after this time on the pack's calendar.
NIGHTLY_AT = time(3)

Action = Literal["built", "rebuilt", "advanced"]

#: The modules whose code decides what a fold of the history holds: what an event is (the events
#: query), what it earns (pricing, the action categories, an audit's claims, the vocabulary), the
#: curve, the quest rewards, the fold and its rows.
FOLD_MODULES = (
    "embry0.execution.skill_actions",
    "embry0.services.context_audit",
    "embry0.services.progression",
    "embry0.services.progression_pack",
    "embry0.services.progression_rollup",
    "embry0.services.progression_vocabulary",
    "embry0.services.quests",
    "embry0.storage.repositories.progression",
    "embry0.storage.repositories.progression_rollup",
)


def fold_version(modules: Sequence[str] = FOLD_MODULES) -> str:
    """A digest of the code that folds the history, so a rollup another release folded is not read as this one's.

    The pack hash covers what an operator can change; this covers what a
    release can. Any change to one of the modules, a comment included,
    rebuilds the rollup once, which costs one read of the history.
    """
    digest = hashlib.sha256()
    for name in modules:
        spec = importlib.util.find_spec(name)
        origin = spec.origin if spec is not None else None
        digest.update(name.encode() + b"\0")
        digest.update(Path(origin).read_bytes() if origin and origin.endswith(".py") else b"no source")
    return digest.hexdigest()[:16]


FOLD_VERSION = fold_version()


class EventSource(Protocol):
    async def events(self, *, since: datetime | None = None, until: datetime | None = None) -> list[ProgressEvent]: ...

    async def journal(self, agents: Collection[str] | None = None) -> Journal: ...


class RollupWriting(Protocol):
    """Writes made under the rollup's lock, in one transaction (``ProgressionRollupRepository.writing``)."""

    async def load(self, pack_hash: str) -> StoredRollup | None: ...

    async def replace(self, head: RollupHead, daily: Sequence[DailyXp], milestones: Sequence[Milestone]) -> None: ...

    async def append(self, head: RollupHead, daily: Sequence[DailyXp], milestones: Sequence[Milestone]) -> None: ...


class RollupStore(Protocol):
    """Where the rollup is kept: ``ProgressionRollupRepository`` in production."""

    async def load(self, pack_hash: str) -> StoredRollup | None: ...

    def writing(self) -> AbstractAsyncContextManager[RollupWriting]: ...


def day_start(day: date, rules: Rules) -> datetime:
    """The instant ``day`` begins on the pack's calendar, in UTC."""
    return datetime.combine(day, time(0), tzinfo=rules.zone).astimezone(UTC)


def closed_through(now: datetime, rules: Rules) -> tuple[date, datetime]:
    """The newest day of the pack's calendar that ended at least :data:`SETTLE` before ``now``, and when it ended."""
    cutoff = now - SETTLE
    day = rules.local(cutoff).date()
    while day_start(day, rules) > cutoff:
        day -= timedelta(days=1)
    return day - timedelta(days=1), day_start(day, rules)


def last_nightly(now: datetime, rules: Rules, at: time = NIGHTLY_AT) -> datetime:
    """The latest time ``at`` on the pack's calendar at or before ``now``."""
    local_day = rules.local(now).date()
    last = datetime.combine(local_day, at, tzinfo=rules.zone).astimezone(UTC)
    if last > now:
        last = datetime.combine(local_day - timedelta(days=1), at, tzinfo=rules.zone).astimezone(UTC)
    return last


def _iso(value: datetime) -> str:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


def rewards_json(rewards: Iterable[tuple[datetime, Reward]]) -> list[dict[str, Any]]:
    return [{"at": _iso(at), "agent_type": r.agent_type, "stat": r.stat, "xp": r.xp} for at, r in rewards]


def stored_rewards(head: RollupHead) -> list[tuple[datetime, Reward]]:
    return [
        (datetime.fromisoformat(r["at"]), Reward(str(r["agent_type"]), str(r["stat"]), int(r["xp"])))
        for r in head.rewards
    ]


def before(rewards: Sequence[tuple[datetime, Reward]], boundary: datetime) -> list[tuple[datetime, Reward]]:
    return [pair for pair in rewards if pair[0] < boundary]


def view(head: RollupHead) -> dict[str, Any]:
    """What GET /progression reports of the rollup behind its experience."""
    return {
        "through_day": head.through_day.isoformat(),
        "boundary": _iso(head.boundary),
        "built_at": _iso(head.built_at),
        "rebuilt_at": _iso(head.rebuilt_at),
    }


# ---- a fold and its rows ----


def resume(stored: StoredRollup, *, rules: Rules, agent_types: Iterable[str] = ()) -> Fold:
    """The fold as it stood at the rollup's boundary, with ``agent_types`` as characters too."""
    fold = Fold(rules=rules, agent_types=agent_types)
    state = stored.head.state
    for agent in state.get("characters", ()):
        fold.character(str(agent))
    for (agent, stat), xp in stored.xp.items():
        fold.character(agent).stat_xp[stat] = xp
    for m in stored.milestones:
        if m.kind == "level":
            fold.level_ups.append(LevelUp(m.at, m.agent_type, str(m.skill), int(m.level or 0)))
        elif m.kind == "agent_level":
            fold.agent_level_ups.append(AgentLevelUp(m.at, m.agent_type, int(m.level or 0)))
        elif m.kind == "rare":
            fold.drops.append(RareDrop(m.at, m.agent_type, str(m.rare)))
            fold.character(m.agent_type).rares.setdefault(str(m.rare), m.at)
    fold.agent_levels = {str(a): int(level) for a, level in state.get("agent_levels", {}).items()}
    year = state.get("last_run_year")
    fold.last_run_year = None if year is None else int(year)
    return fold


def _state(fold: Fold) -> dict[str, Any]:
    return {
        "characters": sorted(fold.characters),
        "agent_levels": dict(sorted(fold.agent_levels.items())),
        "last_run_year": fold.last_run_year,
    }


def _daily(fold: Fold) -> list[DailyXp]:
    return [DailyXp(day, agent, stat, xp) for (day, agent, stat), xp in sorted((fold.daily or {}).items())]


def _milestones(fold: Fold, *, levels: int = 0, agent_levels: int = 0, drops: int = 0, seq: int = 0) -> list[Milestone]:
    """The fold's milestones after the first ``levels``, ``agent_levels`` and ``drops``, numbered from ``seq``."""
    rows: list[Milestone] = []
    for up in fold.level_ups[levels:]:
        rows.append(Milestone(seq + len(rows), up.at, "level", up.agent_type, skill=up.stat, level=up.level))
    for agent_up in fold.agent_level_ups[agent_levels:]:
        rows.append(Milestone(seq + len(rows), agent_up.at, "agent_level", agent_up.agent_type, level=agent_up.level))
    for drop in fold.drops[drops:]:
        rows.append(Milestone(seq + len(rows), drop.at, "rare", drop.agent_type, rare=drop.rare))
    return rows


@dataclass(frozen=True)
class Levels:
    """What a read got from the rollup: the levels, or None when it must read every raw row."""

    progression: Progression | None
    head: RollupHead | None
    #: A write the rollup needs, which the caller schedules in the background.
    due: bool


@dataclass(frozen=True)
class Maintained:
    action: Action | None
    head: RollupHead


class ProgressionRollup:
    """Reads the levels from the stored fold and the raw events after it, and keeps the stored fold current."""

    def __init__(
        self,
        store: RollupStore,
        source: EventSource,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        nightly_at: time | None = NIGHTLY_AT,
        version: str = FOLD_VERSION,
    ) -> None:
        """``nightly_at`` None leaves out the nightly rebuild; the other rebuilds stay. ``version`` is the fold's."""
        self._store = store
        self._source = source
        self.now = now
        self._nightly_at = nightly_at
        self._version = version

    def _nightly_due(self, head: RollupHead, now: datetime, rules: Rules) -> bool:
        return self._nightly_at is not None and head.rebuilt_at < last_nightly(now, rules, self._nightly_at)

    async def levels(
        self, rules: Rules, agent_types: Iterable[str], rewards: Sequence[tuple[datetime, Reward]]
    ) -> Levels:
        """The levels from the stored fold plus the events after its boundary; no progression when it is unusable.

        A missing rollup, one another release folded, or one whose folded
        quest rewards are no longer the ones due before its boundary, gives no
        progression and ``due``: the caller reads every raw row this time, and
        the write fixes the rollup.
        """
        stored = await self._store.load(rules.hash)
        if stored is None or stored.head.fold_version != self._version:
            return Levels(None, None, due=True)
        head = stored.head
        if stored_rewards(head) != before(rewards, head.boundary):
            return Levels(None, None, due=True)
        fold = resume(stored, rules=rules, agent_types=agent_types)
        fold.feed(await self._source.events(since=head.boundary), [r for r in rewards if r[0] >= head.boundary])
        progression = fold.progression()
        progression.rollup = view(head)
        now = self.now()
        due = self._nightly_due(head, now, rules) or closed_through(now, rules)[1] > head.boundary
        return Levels(progression, head, due=due)

    async def maintain(self, rules: Rules, *, force_since: datetime | None = None) -> Maintained:
        """Build, advance or rebuild the stored fold for ``rules``, whichever is due, under the rollup's lock.

        A second caller waits for the first and finds nothing left to do.
        ``force_since`` asks for a full rebuild unless one finished after it.
        """
        async with self._store.writing() as writer:
            return await self._maintain(writer, rules, force_since)

    async def _maintain(self, writer: RollupWriting, rules: Rules, force_since: datetime | None) -> Maintained:
        stored = await writer.load(rules.hash)
        now = self.now()
        through, boundary = closed_through(now, rules)
        rewards = quest_rewards(await self._source.journal(run_agents(rules)), rules=rules)
        full = (
            stored is None
            or stored.head.fold_version != self._version
            or (force_since is not None and stored.head.rebuilt_at < force_since)
            or self._nightly_due(stored.head, now, rules)
            or stored_rewards(stored.head) != before(rewards, stored.head.boundary)
        )
        if stored is None or full:
            fold = Fold(rules=rules)
            fold.daily = {}
            folded = before(rewards, boundary)
            fold.feed(await self._source.events(until=boundary), folded)
            head = RollupHead(
                pack_hash=rules.hash,
                fold_version=self._version,
                timezone=rules.pack.timezone,
                through_day=through,
                boundary=boundary,
                state=_state(fold),
                rewards=rewards_json(folded),
                built_at=now,
                rebuilt_at=now,
            )
            await writer.replace(head, _daily(fold), _milestones(fold))
            return Maintained("built" if stored is None else "rebuilt", head)
        if boundary <= stored.head.boundary:
            return Maintained(None, stored.head)
        fold = resume(stored, rules=rules)
        counts = {"levels": len(fold.level_ups), "agent_levels": len(fold.agent_level_ups), "drops": len(fold.drops)}
        fold.daily = {}
        old = stored.head.boundary
        fold.feed(
            await self._source.events(since=old, until=boundary),
            [r for r in rewards if old <= r[0] < boundary],
        )
        head = replace(
            stored.head,
            through_day=through,
            boundary=boundary,
            state=_state(fold),
            rewards=rewards_json(before(rewards, boundary)),
            built_at=now,
        )
        seq = stored.milestones[-1].seq + 1 if stored.milestones else 0
        await writer.append(head, _daily(fold), _milestones(fold, **counts, seq=seq))
        return Maintained("advanced", head)
