"""The progression rollup (#176): the closed days folded once read exactly as one fold over everything.

A model-based property test drives :class:`ProgressionRollup` over random
histories with an in-memory store: events that arrive as a clock moves, some
of them days after their own time, quest rewards that move, a pack change and
the new year. The model knows which events each write folded, so it predicts
the rollup's answer exactly, staleness included; with nothing late it is a
full recompute's answer. The database round trip is pinned in
``tests/unit/storage/test_progression_rollup_repo.py``.
"""

from __future__ import annotations

import asyncio
import json
import random
import time as time_module
from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import asyncpg
import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from embry0.execution.skill_actions import ACTION_KEYS, LEGACY_ACTION_KEYS
from embry0.services.progression import Progression, ProgressionService, compute
from embry0.services.progression_pack import Rules, default_rules, default_text, parse, rules_for
from embry0.services.progression_rollup import (
    SETTLE,
    ProgressionRollup,
    closed_through,
    last_nightly,
)
from embry0.services.quests import Reward, quest_rewards, run_agents
from embry0.storage.repositories.progression import FINISHED_WELL, Fact, JobRow, Journal, ProgressEvent, RunRow
from embry0.storage.repositories.progression_rollup import DailyXp, Milestone, RollupHead, StoredRollup

RULES = default_rules()


def _pack(**changes: Any) -> Rules:
    data = yaml.safe_load(default_text())
    if "timezone" in changes:
        data["timezone"] = changes["timezone"]
    if "merged_xp" in changes:
        data["rates"]["outcomes"]["merged"]["xp"] = changes["merged_xp"]
    if "edit_xp" in changes:
        data["rates"]["actions"]["edit"] = changes["edit_xp"]
    return rules_for(parse(data))


# ---- in-memory stand-ins for the repositories ----

#: The order the events query gives one time's events: its UNION's branch order.
BRANCH = ("run", "merged", "opened", "cleaned", "comeback", "resumed", "qa_passed", "audited", "asked")


@dataclass(frozen=True)
class Item:
    """An event, when its row became visible, and its place in the events query's order."""

    event: ProgressEvent
    recorded: datetime
    key: int

    @property
    def order(self) -> tuple[datetime, int, int]:
        return (self.event.at, BRANCH.index(self.event.source), self.key)


@dataclass(frozen=True)
class JobItem:
    job: JobRow
    recorded: datetime


@dataclass(frozen=True)
class RunItem:
    run: RunRow
    recorded: datetime


@dataclass
class World:
    """A history revealed as its clock moves: a row is visible once it was recorded."""

    items: list[Item]
    jobs: list[JobItem]
    runs: list[RunItem]
    agents: list[str]
    clock: datetime
    event_reads: list[tuple[datetime | None, datetime | None]] = field(default_factory=list)

    def visible(self) -> list[Item]:
        return sorted((i for i in self.items if i.recorded <= self.clock), key=lambda i: i.order)

    def journal(self, agents: Collection[str] | None = None) -> Journal:
        return Journal(
            [j.job for j in self.jobs if j.recorded <= self.clock],
            [r.run for r in self.runs if r.recorded <= self.clock and (agents is None or r.run.agent_type in agents)],
        )


class Source:
    """The repository surface over a World, with ``journal`` under its name."""

    def __init__(self, world: World) -> None:
        self.world = world

    async def events(self, *, since: datetime | None = None, until: datetime | None = None) -> list[ProgressEvent]:
        self.world.event_reads.append((since, until))
        return [
            i.event
            for i in self.world.visible()
            if (since is None or i.event.at >= since) and (until is None or i.event.at < until)
        ]

    async def journal(self, agents: Collection[str] | None = None) -> Journal:
        return self.world.journal(agents)

    async def agent_types(self) -> list[str]:
        return list(self.world.agents)

    async def facts(self, queries: object, timezone: str) -> list[Fact]:
        return []


class MemoryStore:
    """The rollup tables in memory: daily rows added up on load, milestones in their order, one writer at a time."""

    def __init__(self) -> None:
        self.heads: dict[str, RollupHead] = {}
        self.daily: dict[str, dict[tuple[date, str, str], int]] = {}
        self.milestones: dict[str, list[Milestone]] = {}
        self.lock = asyncio.Lock()
        self.writes: list[str] = []

    async def load(self, pack_hash: str) -> StoredRollup | None:
        head = self.heads.get(pack_hash)
        if head is None:
            return None
        xp: dict[tuple[str, str], int] = {}
        for (_day, agent, skill), gained in self.daily[pack_hash].items():
            xp[(agent, skill)] = xp.get((agent, skill), 0) + gained
        # Through JSON, as the database's JSONB columns hand them back.
        head = RollupHead(
            **{
                **head.__dict__,
                "state": json.loads(json.dumps(head.state)),
                "rewards": json.loads(json.dumps(head.rewards)),
            }
        )
        return StoredRollup(head, xp, sorted(self.milestones[pack_hash], key=lambda m: m.seq))

    @asynccontextmanager
    async def writing(self) -> AsyncIterator[MemoryWriter]:
        async with self.lock:
            yield MemoryWriter(self)


class MemoryWriter:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def load(self, pack_hash: str) -> StoredRollup | None:
        return await self._store.load(pack_hash)

    async def replace(self, head: RollupHead, daily: Sequence[DailyXp], milestones: Sequence[Milestone]) -> None:
        store = self._store
        store.heads = {head.pack_hash: head}
        store.daily = {head.pack_hash: {}}
        store.milestones = {head.pack_hash: []}
        self._add(head.pack_hash, daily, milestones)
        store.writes.append("replace")

    async def append(self, head: RollupHead, daily: Sequence[DailyXp], milestones: Sequence[Milestone]) -> None:
        assert head.pack_hash in self._store.heads, "an advance needs a rollup"
        self._store.heads[head.pack_hash] = head
        self._add(head.pack_hash, daily, milestones)
        self._store.writes.append("append")

    def _add(self, pack_hash: str, daily: Sequence[DailyXp], milestones: Sequence[Milestone]) -> None:
        rows = self._store.daily[pack_hash]
        for d in daily:
            rows[(d.day, d.agent_type, d.skill)] = rows.get((d.day, d.agent_type, d.skill), 0) + d.xp
        seqs = {m.seq for m in self._store.milestones[pack_hash]}
        for m in milestones:
            assert m.seq not in seqs, f"milestone seq {m.seq} written twice"
            seqs.add(m.seq)
        self._store.milestones[pack_hash].extend(milestones)


# ---- comparing two answers ----


def differences(actual: Progression, expected: Progression) -> list[str]:
    """Every field in which ``actual`` differs from ``expected``: totals, levels, milestones at their exact times."""
    problems: list[str] = []
    a, e = actual.to_dict(recent=1_000_000), expected.to_dict(recent=1_000_000)
    for body in (a, e):
        body.pop("computed_at")
        body.pop("rollup")
    for key in sorted(set(a) | set(e)):
        if a.get(key) != e.get(key):
            problems.append(f"to_dict()[{key!r}] differs: {str(a.get(key))[:300]} != {str(e.get(key))[:300]}")
    for name in ("level_ups", "agent_level_ups", "drops"):
        if getattr(actual, name) != getattr(expected, name):
            problems.append(f"{name} differ")
    stat_xp = {k: c.stat_xp for k, c in actual.characters.items()}
    expected_xp = {k: c.stat_xp for k, c in expected.characters.items()}
    if stat_xp != expected_xp:
        problems.append(f"stat_xp differs: {stat_xp} != {expected_xp}")
    return problems


# ---- a random history ----

AGENTS = ("developer", "review", "qa", "triage", "slopmaster", "context-auditor", "my-custom-agent")
KEYS = sorted(ACTION_KEYS) + sorted(LEGACY_ACTION_KEYS)
START = datetime(2025, 12, 18, tzinfo=UTC)
DAYS = 26


def _event(rng: random.Random, source: str, at: datetime) -> ProgressEvent:
    agent: str | None = rng.choice(AGENTS)
    report = None
    tools = actions = None
    if source == "run":
        if rng.random() < 0.85:
            actions = {rng.choice(KEYS): rng.randint(1, 60) for _ in range(rng.randint(0, 4))}
        else:
            tools = {rng.choice(("Bash", "Edit", "Read", "Grep", "WebFetch")): rng.randint(1, 30)}
    elif source in ("merged", "opened", "comeback"):
        agent = rng.choice(("developer", "slopmaster"))
    elif source == "cleaned":
        agent = "slopmaster"
        report = json.dumps({"added": rng.randint(0, 50), "removed": rng.randint(0, 400)})
    elif source == "qa_passed":
        agent = "qa"
    elif source == "audited":
        agent = "context-auditor"
        report = f"Stale: {rng.randint(0, 20)}\nFiles: {rng.randint(1, 9)}\n"
    elif source == "resumed" and rng.random() < 0.2:
        agent = None  # a resumed job with no run: work no character did
    return ProgressEvent(source, agent, at, report, tools, actions)


def _lateness(rng: random.Random, source: str) -> timedelta:
    roll = rng.random()
    if roll < 0.08:
        return timedelta(hours=rng.uniform(SETTLE.total_seconds() / 3600 + 1, 130))  # recorded days late
    if roll < 0.14:
        return timedelta(hours=rng.uniform(20, 47))  # late, inside the window
    if source == "run":
        return timedelta(minutes=rng.uniform(0, 180))  # a run is recorded when it finishes
    return timedelta(0)


def random_world(seed: int) -> World:
    rng = random.Random(seed)
    items: list[Item] = []
    for i in range(rng.randint(80, 220)):
        source = rng.choice(BRANCH + ("run", "run", "run", "merged"))
        if items and rng.random() < 0.12:
            at = rng.choice(items).event.at  # two events at one time
        else:
            at = START + timedelta(seconds=rng.randrange(DAYS * 86_400), microseconds=rng.randrange(1_000_000))
        items.append(Item(_event(rng, source, at), at + _lateness(rng, source), i))
    # The days the rares fall on, and the turn of the year, in every timezone the tests use.
    for i, at in enumerate(
        (
            datetime(2025, 12, 25, 9, tzinfo=UTC),
            datetime(2025, 12, 31, 13, tzinfo=UTC),
            datetime(2026, 1, 1, 0, 30, tzinfo=UTC),
            datetime(2026, 1, 1, 11, 15, tzinfo=UTC),
        )
    ):
        source = "merged" if i == 0 else "run"
        items.append(Item(_event(rng, source, at), at + _lateness(rng, source), 10_000 + i))

    jobs: list[JobItem] = []
    runs: list[RunItem] = []

    def when() -> datetime:
        return START + timedelta(seconds=rng.randrange(DAYS * 86_400))

    for n in range(rng.randint(4, 14)):
        issue = f"iss-{rng.randint(1, 5)}"
        repo = rng.choice(("acme/widgets", "acme/lib"))
        created = when()
        at = created + timedelta(hours=rng.uniform(0.1, 6))
        status = rng.choice(("failed", "pr_merged", "completed", "pr_merged"))
        template = rng.choice((None, None, "qa", "onboard"))
        # A job is often recorded days late here, so a quest's completion moves into the folded days.
        late = timedelta(hours=rng.uniform(50, 120)) if rng.random() < 0.35 else _lateness(rng, "merged")
        jobs.append(
            JobItem(JobRow(repo, issue if rng.random() < 0.8 else None, template, status, created, at), at + late)
        )
        if rng.random() < 0.5:
            agent = rng.choice(("review", "pr-reviewer", "qa", "triage", "developer"))
            run_at = created + timedelta(minutes=rng.uniform(1, 60))
            runs.append(RunItem(RunRow(agent, repo, issue, run_at), run_at + _lateness(rng, "run")))
    # A failure and a merge on one issue early on, recorded days later: Try, Try Again completes in the past.
    for n in range(rng.randint(1, 2)):
        failed_at = START + timedelta(hours=rng.uniform(1, 30))
        merged_at = failed_at + timedelta(hours=rng.uniform(1, 20))
        recorded = START + timedelta(days=rng.uniform(5, 15))
        for status, at in (("failed", failed_at), ("pr_merged", merged_at)):
            jobs.append(JobItem(JobRow("acme/widgets", f"iss-late-{n}", None, status, at, at), recorded))
    agents = sorted(rng.sample(AGENTS, rng.randint(1, len(AGENTS))))
    return World(items, jobs, runs, agents, clock=START + timedelta(days=rng.uniform(3, 6)))


# ---- the model ----


@dataclass
class Model:
    """What the stored rollup holds, as the test tracks it: the events each write folded."""

    rules: Rules
    folded: set[int] = field(default_factory=set)
    boundary: datetime | None = None
    rewards: list[tuple[datetime, Reward]] | None = None

    def expected(self, world: World, rewards: list[tuple[datetime, Reward]]) -> Progression | None:
        """The rollup's answer: what it folded before its boundary and every visible event after it."""
        if self.boundary is None or self.rewards != [r for r in rewards if r[0] < self.boundary]:
            return None
        boundary = self.boundary
        items = [i for i in world.visible() if i.key in self.folded or i.event.at >= boundary]
        return compute([i.event for i in items], world.agents, rewards, rules=self.rules)

    def wrote(self, action: str | None, head: RollupHead, world: World, rewards: list[tuple[datetime, Reward]]) -> None:
        if action is None:
            return
        old = self.boundary
        new = head.boundary
        if action in ("built", "rebuilt"):
            self.folded = {i.key for i in world.visible() if i.event.at < new}
        else:
            assert old is not None
            self.folded |= {i.key for i in world.visible() if old <= i.event.at < new}
        self.boundary = new
        self.rewards = [r for r in rewards if r[0] < new]


PACKS = {
    "utc": RULES,
    "auckland": _pack(timezone="Pacific/Auckland", merged_xp=75_000),
    "new-york": _pack(timezone="America/New_York", edit_xp=2_100),
}


async def _walk(seed: int, nightly: bool, tally: dict[str, int]) -> None:
    rng = random.Random(seed * 7919 + nightly)
    world = random_world(seed)
    store = MemoryStore()
    rollup = ProgressionRollup(store, Source(world), now=lambda: world.clock, nightly_at=time(3) if nightly else None)
    names = list(PACKS)
    name = rng.choice(names)
    models = {n: Model(PACKS[n]) for n in names}
    for step in range(18):
        rules = PACKS[name]
        model = models[name]
        rewards = quest_rewards(world.journal(run_agents(rules)), rules=rules)
        context = f"seed {seed}, nightly {nightly}, step {step}, pack {name}, clock {world.clock.isoformat()}"
        for phase in ("read", "write", "read again"):
            if phase == "write":
                if rng.random() < 0.3:
                    continue
                done = await rollup.maintain(rules)
                tally[done.action or "nothing"] = tally.get(done.action or "nothing", 0) + 1
                assert done.head.boundary == closed_through(world.clock, rules)[1], context
                if done.action in ("built", "rebuilt"):
                    for other in models.values():
                        if other is not model:  # a full write deletes every other pack's rollup
                            other.boundary, other.folded, other.rewards = None, set(), None
                model.wrote(done.action, done.head, world, rewards)
                continue
            levels = await rollup.levels(rules, world.agents, rewards)
            expected = model.expected(world, rewards)
            if expected is None:
                assert levels.progression is None, f"{context}: answered from a rollup the model has none of"
                reason = "raw" if model.boundary is None else "reward moved"
                tally[reason] = tally.get(reason, 0) + 1
                continue
            assert levels.progression is not None, f"{context}, {phase}: the rollup was not used"
            problems = differences(levels.progression, expected)
            assert not problems, (
                f"{context}, {phase}: the rollup and the fold over what it holds differ:\n" + "\n".join(problems[:10])
            )
            full = compute([i.event for i in world.visible()], world.agents, rewards, rules=rules)
            tally["stale" if differences(levels.progression, full) else "exact"] += 1
            boundary = model.boundary
            assert boundary is not None
            tally["tail"] += any(i.event.at >= boundary for i in world.visible())
            tally["folded"] += bool(model.folded)
        world.clock += timedelta(hours=rng.uniform(0.5, 8) if rng.random() < 0.5 else rng.uniform(8, 44))
        if rng.random() < 0.12:
            name = rng.choice(names)
    # A forced rebuild folds every late event: the answer is a full recompute's.
    rules = PACKS[name]
    rewards = quest_rewards(world.journal(run_agents(rules)), rules=rules)
    await rollup.maintain(rules, force_since=world.clock)
    levels = await rollup.levels(rules, world.agents, rewards)
    assert levels.progression is not None
    full = compute([i.event for i in world.visible()], world.agents, rewards, rules=rules)
    problems = differences(levels.progression, full)
    assert not problems, f"seed {seed}: after a rebuild the rollup differs from a full recompute:\n" + "\n".join(
        problems[:10]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("nightly", [True, False], ids=["nightly", "no-nightly"])
async def test_the_rollup_answers_what_one_fold_over_what_it_holds_answers(nightly: bool) -> None:
    """For random histories: after a build, advances, rebuilds, late events, moved rewards and pack changes."""
    tally: dict[str, int] = {"exact": 0, "stale": 0, "tail": 0, "folded": 0}
    for seed in range(60):
        await _walk(seed, nightly, tally)
    # The walk must reach every case, or it proves less than it claims.
    assert tally["exact"] > 100 and tally["tail"] > 100 and tally["folded"] > 100, tally
    assert tally["stale"] > 5, f"no late event was ever missed: the staleness went untested: {tally}"
    assert tally.get("built", 0) > 30 and tally.get("raw", 0) > 30, tally
    assert tally.get("advanced", 0) > 20, f"no advance: {tally}"
    assert tally.get("reward moved", 0) > 10, f"no quest reward ever moved into the folded days: {tally}"
    assert tally.get("rebuilt", 0) > 10, f"no rebuild: {tally}"


def test_the_property_walk_crosses_the_year_and_the_rares() -> None:
    """The walk's histories hold a holiday merge and runs on both sides of the new year, on each pack's calendar."""
    world = random_world(0)
    for rules in PACKS.values():
        merged = {
            (rules.local(i.event.at).month, rules.local(i.event.at).day)
            for i in world.items
            if i.event.source == "merged"
        }
        assert (12, 25) in merged, f"no merge on 25 December in {rules.pack.timezone}"
        years = {rules.local(i.event.at).year for i in world.items if i.event.source == "run"}
        assert years == {2025, 2026}, f"the runs do not cross the year in {rules.pack.timezone}: {years}"


# ---- the calendar ----


def test_a_day_closes_48_hours_after_it_ends_on_the_packs_calendar() -> None:
    now = datetime(2026, 3, 10, 13, 0, tzinfo=UTC)
    assert closed_through(now, RULES) == (date(2026, 3, 7), datetime(2026, 3, 8, tzinfo=UTC))
    assert closed_through(datetime(2026, 3, 10, 0, 0, tzinfo=UTC), RULES) == (
        date(2026, 3, 7),
        datetime(2026, 3, 8, tzinfo=UTC),
    ), "the day that ended exactly 48 hours ago is closed"
    tokyo = _pack(timezone="Asia/Tokyo")
    day, boundary = closed_through(now, tokyo)
    assert day == date(2026, 3, 7) and boundary == datetime(2026, 3, 7, 15, tzinfo=UTC), "midnight in Tokyo"
    assert now - boundary >= SETTLE


def test_the_nightly_rebuild_is_at_three_on_the_packs_calendar() -> None:
    assert last_nightly(datetime(2026, 3, 10, 2, 59, tzinfo=UTC), RULES) == datetime(2026, 3, 9, 3, tzinfo=UTC)
    assert last_nightly(datetime(2026, 3, 10, 3, 0, tzinfo=UTC), RULES) == datetime(2026, 3, 10, 3, tzinfo=UTC)
    new_york = _pack(timezone="America/New_York")
    assert last_nightly(datetime(2026, 3, 10, 12, tzinfo=UTC), new_york) == datetime(2026, 3, 10, 7, tzinfo=UTC)


# ---- the service: reads never wait for a write ----


def _world_at(clock: datetime) -> World:
    events = [
        Item(
            ProgressEvent("merged", "developer", datetime(2026, 3, 1, 10, tzinfo=UTC)),
            datetime(2026, 3, 1, 10, tzinfo=UTC),
            1,
        ),
        Item(
            ProgressEvent("run", "review", datetime(2026, 3, 5, 9, tzinfo=UTC), skill_actions={"read:files": 40}),
            datetime(2026, 3, 5, 9, 30, tzinfo=UTC),
            2,
        ),
        Item(
            ProgressEvent("merged", "developer", datetime(2026, 3, 9, 8, tzinfo=UTC)),
            datetime(2026, 3, 9, 8, tzinfo=UTC),
            3,
        ),
    ]
    return World(events, [], [], ["developer", "review"], clock=clock)


class Packs:
    def __init__(self, rules: Rules) -> None:
        self.rules = rules

    async def active(self) -> Any:
        return self.rules.pack


@pytest.mark.asyncio
async def test_a_read_answers_from_every_row_and_builds_in_the_background() -> None:
    world = _world_at(datetime(2026, 3, 10, 13, tzinfo=UTC))
    store = MemoryStore()
    service = ProgressionService(
        Source(world), packs=Packs(RULES), rollups=store, ttl_seconds=0, now=lambda: world.clock
    )
    first = await service.read()
    assert first.rollup is None and first.to_dict()["rollup"] is None, "no rollup yet: every raw row was read"
    assert store.writes == [], "the read did not wait for the build"
    await service.settle()
    assert store.writes == ["replace"], "the read started the build"
    world.event_reads.clear()
    second = await service.read()
    assert second.to_dict()["rollup"] == {
        "through_day": "2026-03-07",
        "boundary": "2026-03-08T00:00:00+00:00",
        "built_at": "2026-03-10T13:00:00+00:00",
        "rebuilt_at": "2026-03-10T13:00:00+00:00",
    }, "the answer says which days the rollup holds and when it was built"
    assert not differences(second, first), "the rollup answers what every raw row answered"
    assert world.event_reads == [(datetime(2026, 3, 8, tzinfo=UTC), None)], (
        f"a steady read asks only for the events after the boundary: {world.event_reads}"
    )


@pytest.mark.asyncio
async def test_a_changed_pack_builds_its_own_rollup_and_drops_the_old_one() -> None:
    world = _world_at(datetime(2026, 3, 10, 13, tzinfo=UTC))
    store = MemoryStore()
    packs = Packs(RULES)
    service = ProgressionService(Source(world), packs=packs, rollups=store, ttl_seconds=0, now=lambda: world.clock)
    await service.read()
    await service.settle()
    assert set(store.heads) == {RULES.hash}
    other = _pack(merged_xp=90_000, timezone="Asia/Tokyo")
    packs.rules = other
    changed = await service.read()
    assert changed.rollup is None, "a new pack is read from every raw row until its rollup is built"
    assert changed.characters["developer"].stat_xp["coding"] == 180_000, "and priced under the new pack at once"
    await service.settle()
    assert set(store.heads) == {other.hash}, f"the old pack's rollup is gone and the new one built: {set(store.heads)}"
    rebuilt = await service.read()
    assert rebuilt.rollup is not None and rebuilt.rollup["boundary"] == "2026-03-07T15:00:00+00:00", rebuilt.rollup
    assert not differences(rebuilt, changed)


@pytest.mark.asyncio
async def test_a_late_event_counts_from_the_nightly_rebuild() -> None:
    """Recorded more than 48 hours late, for a day already folded: stale until the night, and the answer says how old."""
    world = _world_at(datetime(2026, 3, 10, 13, tzinfo=UTC))
    store = MemoryStore()
    service = ProgressionService(
        Source(world), packs=Packs(RULES), rollups=store, ttl_seconds=0, now=lambda: world.clock
    )
    await service.read()
    await service.settle()
    world.clock = datetime(2026, 3, 10, 20, tzinfo=UTC)
    late = Item(ProgressEvent("merged", "developer", datetime(2026, 3, 2, 10, tzinfo=UTC)), world.clock, 4)
    recent = Item(ProgressEvent("merged", "developer", datetime(2026, 3, 9, 10, tzinfo=UTC)), world.clock, 5)
    world.items += [late, recent]
    stale = await service.read()
    assert stale.characters["developer"].stat_xp["coding"] == 180_000, (
        "the merge inside the window counts at once; the one recorded eight days late does not yet"
    )
    assert stale.rollup is not None and stale.rollup["rebuilt_at"] == "2026-03-10T13:00:00+00:00"
    assert datetime.fromisoformat(stale.rollup["boundary"]) > late.event.at, "the late merge is before the boundary"
    world.clock = datetime(2026, 3, 11, 3, 5, tzinfo=UTC)
    await service.read()
    await service.settle()
    fresh = await service.read()
    assert fresh.characters["developer"].stat_xp["coding"] == 240_000, "the nightly rebuild folded the late merge"
    assert fresh.rollup is not None and fresh.rollup["rebuilt_at"] == "2026-03-11T03:05:00+00:00"


@pytest.mark.asyncio
async def test_a_quest_reward_that_moves_into_the_folded_days_forces_a_rebuild() -> None:
    world = _world_at(datetime(2026, 3, 10, 13, tzinfo=UTC))
    store = MemoryStore()
    service = ProgressionService(
        Source(world), packs=Packs(RULES), rollups=store, ttl_seconds=0, now=lambda: world.clock
    )
    await service.read()
    await service.settle()
    assert store.writes == ["replace"]
    # Try, Try Again completes on 3 March, inside the folded days, once both jobs are recorded.
    failed = JobRow(
        "acme/widgets", "iss-1", None, "failed", datetime(2026, 3, 2, tzinfo=UTC), datetime(2026, 3, 2, 1, tzinfo=UTC)
    )
    merged = JobRow(
        "acme/widgets",
        "iss-1",
        None,
        "pr_merged",
        datetime(2026, 3, 3, tzinfo=UTC),
        datetime(2026, 3, 3, 2, tzinfo=UTC),
    )
    world.jobs += [JobItem(failed, world.clock), JobItem(merged, world.clock)]
    moved = await service.read()
    assert moved.rollup is None, "a reward that moved into the folded days: the read folds every raw row"
    assert moved.characters["developer"].stat_xp["recovery"] == 60_000, "and the reward counts at once"
    await service.settle()
    assert store.writes == ["replace", "replace"], "the moved reward rebuilt the rollup"
    again = await service.read()
    assert again.rollup is not None and not differences(again, moved)


@pytest.mark.asyncio
async def test_a_write_that_fails_leaves_the_reads_going() -> None:
    world = _world_at(datetime(2026, 3, 10, 13, tzinfo=UTC))

    class Broken(MemoryStore):
        @asynccontextmanager
        async def writing(self) -> AsyncIterator[MemoryWriter]:
            raise RuntimeError("the database went away")
            yield MemoryWriter(self)  # pragma: no cover

    service = ProgressionService(
        Source(world), packs=Packs(RULES), rollups=Broken(), ttl_seconds=0, now=lambda: world.clock
    )
    first = await service.read()
    await service.settle()
    second = await service.read()
    assert second.rollup is None and not differences(second, first), "a failed build is retried; reads still answer"


@pytest.mark.asyncio
async def test_a_write_that_finishes_during_the_read_that_started_it_is_not_hidden_by_the_cache() -> None:
    """The read that schedules the build goes on reading facts; the build can finish first and must win."""
    world = _world_at(datetime(2026, 3, 10, 13, tzinfo=UTC))

    class SlowFacts(Source):
        async def facts(self, queries: object, timezone: str) -> list[Fact]:
            for _ in range(20):
                await asyncio.sleep(0)  # a database round trip: the background build runs meanwhile
            return []

    store = MemoryStore()
    service = ProgressionService(
        SlowFacts(world), packs=Packs(RULES), rollups=store, ttl_seconds=600, now=lambda: world.clock
    )
    first = await service.read()
    assert first.rollup is None and store.writes == ["replace"], "the build finished while the first read went on"
    await service.settle()
    second = await service.read()
    assert second.rollup is not None, "the first read cached its raw answer over the build that finished during it"


@pytest.mark.asyncio
async def test_a_failed_write_waits_longer_each_time_before_it_is_tried_again() -> None:
    """Each try of a failing build is a full fold of history: not one on every read."""
    world = _world_at(datetime(2026, 3, 10, 13, tzinfo=UTC))
    tries: list[float] = []
    clock = [1_000.0]

    class Broken(MemoryStore):
        @asynccontextmanager
        async def writing(self) -> AsyncIterator[MemoryWriter]:
            tries.append(clock[0])
            raise RuntimeError("the database went away")
            yield MemoryWriter(self)  # pragma: no cover

    service = ProgressionService(
        Source(world),
        packs=Packs(RULES),
        rollups=Broken(),
        ttl_seconds=0,
        clock=lambda: clock[0],
        now=lambda: world.clock,
    )
    for step in (0, 30, 31, 60, 60, 120):  # seconds between reads: 1,000, 1,030, 1,061, 1,121, 1,181, 1,301
        clock[0] += step
        await service.read()
        await service.settle()
    assert tries == [1_000.0, 1_061.0, 1_181.0], (
        f"a failing write is tried after 60 s, then 120 s, not on every read: tried at {tries}"
    )


@pytest.mark.asyncio
async def test_shutdown_waits_a_bounded_time_for_a_write_that_will_not_stop(monkeypatch: Any) -> None:
    import embry0.services.progression as progression_module

    monkeypatch.setattr(progression_module, "SHUTDOWN_WAIT", 0.2, raising=False)
    world = _world_at(datetime(2026, 3, 10, 13, tzinfo=UTC))
    started = asyncio.Event()

    class Stubborn(MemoryStore):
        @asynccontextmanager
        async def writing(self) -> AsyncIterator[MemoryWriter]:
            started.set()
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                await asyncio.sleep(3600)  # ignores the first cancel, as a write stuck in the driver can
            yield MemoryWriter(self)  # pragma: no cover

    service = ProgressionService(
        Source(world), packs=Packs(RULES), rollups=Stubborn(), ttl_seconds=0, now=lambda: world.clock
    )
    await service.read()
    await started.wait()
    began = time_module.monotonic()
    try:
        await asyncio.wait_for(service.aclose(), timeout=3)
    except TimeoutError:
        pass
    finally:
        waited = time_module.monotonic() - began
        task = service._maintenance
        if task is not None:
            task.cancel()
            await asyncio.wait({task}, timeout=1)
    assert waited < 1.5, f"shutdown waited {waited:.1f} s for a write that would not stop; its bound was 0.2 s"


@pytest.mark.asyncio
async def test_an_event_and_a_reward_at_the_boundary_instant_count_once() -> None:
    """The boundary is inclusive for the recent days and exclusive for the folded ones: nothing falls between."""
    world = _world_at(datetime(2026, 3, 10, 13, tzinfo=UTC))
    boundary = datetime(2026, 3, 8, tzinfo=UTC)
    world.items.append(Item(ProgressEvent("merged", "developer", boundary), world.clock, 9))
    # Try, Try Again completes at the boundary instant: its reward lands exactly on it.
    for status, at in (("failed", boundary - timedelta(days=1)), ("pr_merged", boundary)):
        world.jobs.append(JobItem(JobRow("acme/widgets", "iss-edge", None, status, at, at), world.clock))
    store = MemoryStore()
    service = ProgressionService(
        Source(world), packs=Packs(RULES), rollups=store, ttl_seconds=0, now=lambda: world.clock
    )
    raw = await service.read()
    await service.settle()
    rolled = await service.read()
    assert rolled.rollup is not None and rolled.rollup["boundary"] == boundary.isoformat()
    xp = rolled.characters["developer"].stat_xp
    assert (xp.get("coding"), xp.get("recovery")) == (180_000, 60_000), (
        f"the merge and the reward at the boundary each count once: {xp}"
    )
    assert not differences(rolled, raw)


@pytest.mark.asyncio
async def test_a_forced_rebuild_waits_for_its_write_and_says_so() -> None:
    world = _world_at(datetime(2026, 3, 10, 13, tzinfo=UTC))
    store = MemoryStore()
    service = ProgressionService(
        Source(world), packs=Packs(RULES), rollups=store, ttl_seconds=600, now=lambda: world.clock
    )
    cached = await service.read()
    await service.settle()
    world.clock += timedelta(minutes=5)
    result = await service.rebuild()
    assert result["action"] == "rebuilt" and result["rollup"]["rebuilt_at"] == "2026-03-10T13:05:00+00:00", result
    assert await service.read() is not cached, "the cached answer is dropped after a rebuild"
    none = ProgressionService(Source(world), packs=Packs(RULES), rollups=None)
    with pytest.raises(RuntimeError, match="no rollup"):
        await none.rebuild()


@pytest.mark.asyncio
async def test_a_rollup_another_release_folded_is_rebuilt_not_read() -> None:
    """A release can change what an event earns without changing the pack: its rollup is not this release's."""
    world = _world_at(datetime(2026, 3, 10, 13, tzinfo=UTC))
    store = MemoryStore()
    old = ProgressionRollup(store, Source(world), now=lambda: world.clock, version="old-release")
    assert (await old.maintain(RULES)).action == "built"
    new = ProgressionRollup(store, Source(world), now=lambda: world.clock, version="new-release")
    levels = await new.levels(RULES, world.agents, [])
    assert levels.progression is None and levels.due, "another release's rollup was read as this one's"
    done = await new.maintain(RULES)
    assert done.action == "rebuilt" and done.head.fold_version == "new-release", done
    assert (await new.levels(RULES, world.agents, [])).progression is not None
    assert (await old.levels(RULES, world.agents, [])).progression is None, "and the old release no longer reads it"


def test_the_fold_version_follows_the_code_of_every_fold_module(tmp_path: Any, monkeypatch: Any) -> None:
    import importlib.util

    from embry0.services.progression_rollup import FOLD_MODULES, FOLD_VERSION, fold_version

    for name in FOLD_MODULES:
        spec = importlib.util.find_spec(name)
        assert spec is not None and str(spec.origin).endswith(".py"), f"{name} has no source to fingerprint"
    assert fold_version() == FOLD_VERSION, "the version is the code's, not the import's"
    (tmp_path / "foldpkg").mkdir()
    (tmp_path / "foldpkg" / "__init__.py").write_text("")
    module = tmp_path / "foldpkg" / "pricing.py"
    module.write_text("XP = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    before = fold_version(["foldpkg.pricing"])
    module.write_text("XP = 2\n")
    assert fold_version(["foldpkg.pricing"]) != before, "a change to a fold module leaves the version as it was"


@pytest.mark.asyncio
async def test_a_facts_fallback_to_utc_leaves_the_rollup_on_the_packs_calendar() -> None:
    """The rollup's days are computed in Python on the pack's zone; Postgres never applies the timezone to them.

    When the database refuses the pack's timezone, only the achievements' facts fall back to UTC
    (``ProgressionService._facts``): the experience, its days and the rares stay on the pack's calendar,
    as in a full fold.
    """
    berlin = _pack(timezone="Europe/Berlin")
    world = _world_at(datetime(2026, 3, 10, 13, tzinfo=UTC))
    asked: list[str] = []

    class RefusesBerlin(Source):
        async def facts(self, queries: object, timezone: str) -> list[Fact]:
            asked.append(timezone)
            if timezone != "UTC":
                raise asyncpg.InvalidParameterValueError(f'time zone "{timezone}" not recognized')
            return []

    store = MemoryStore()
    service = ProgressionService(
        RefusesBerlin(world), packs=Packs(berlin), rollups=store, ttl_seconds=0, now=lambda: world.clock
    )
    raw = await service.read()
    await service.settle()
    rolled = await service.read()
    assert asked == ["Europe/Berlin", "UTC", "Europe/Berlin", "UTC"], asked
    assert rolled.rollup is not None and rolled.rollup["boundary"] == "2026-03-07T23:00:00+00:00", (
        f"the boundary is not Berlin's midnight: {rolled.rollup}"
    )
    assert not differences(rolled, raw), "the rollup answers what the full fold under the same pack answers"
    assert (rolled.timezone_applied, rolled.to_dict()["pack"]["timezone_applied"]) == ("UTC", "UTC"), (
        "a read through the rollup still says its achievements were counted on UTC's calendar"
    )


# ---- the journal reads only the runs the quests and diaries read ----


def _journal_with_every_agent(rng: random.Random) -> Journal:
    base = datetime(2026, 2, 1, tzinfo=UTC)
    jobs, runs = [], []
    for n in range(40):
        repo = rng.choice(("acme/widgets", "acme/lib"))
        issue = rng.choice(("iss-1", "iss-2", "iss-3", None))
        created = base + timedelta(hours=rng.uniform(0, 600))
        status = rng.choice(("failed", "pr_merged", "completed"))
        template = rng.choice((None, "qa", "onboard", "context-audit"))
        report = "Stale: 0\nFiles: 3\n" if template == "context-audit" and status in FINISHED_WELL else None
        jobs.append(JobRow(repo, issue, template, status, created, created + timedelta(hours=1), report))
        for _ in range(rng.randint(0, 3)):
            agent = rng.choice(("review", "pr-reviewer", "qa", "triage", "developer", "ops", "research"))
            runs.append(RunRow(agent, repo, issue, created + timedelta(minutes=rng.uniform(0, 50))))
    return Journal(jobs, sorted(runs, key=lambda r: r.at))


def test_no_quest_or_diary_reads_the_runs_of_an_agent_it_does_not_name() -> None:
    """``run_agents`` narrows the journal's runs (migration 131's index); the quests and diaries must not notice."""
    from embry0.services.quests import evaluate_diaries, evaluate_quests

    data = yaml.safe_load(default_text())
    data["quests"][2]["params"] = {"review_agents": ["research"], "qa_agents": ["ops"]}
    data["diaries"]["tiers"][0]["tasks"][0]["params"] = {"agents": ["developer"]}
    renamed = rules_for(parse(data))
    for rules in (RULES, renamed):
        named = run_agents(rules)
        for seed in range(30):
            journal = _journal_with_every_agent(random.Random(seed))
            narrowed = Journal(journal.jobs, [r for r in journal.runs if r.agent_type in named])

            def answer(j: Journal, rules: Rules = rules) -> Any:
                diaries = evaluate_diaries(j, rules=rules)
                quests = evaluate_quests(j, diaries, lambda total: None, rules=rules)
                return [d.to_dict() for d in diaries], [q.to_dict() for q in quests], quest_rewards(j, rules=rules)

            assert answer(narrowed) == answer(journal), (
                f"seed {seed}, pack {rules.hash[:8]}: a quest or diary read a run of an agent outside {sorted(named)}"
            )
    assert run_agents(renamed) >= {"research", "ops", "developer"}


# ---- the route ----


@pytest.mark.asyncio
async def test_the_rebuild_route_answers_the_rollup_it_wrote() -> None:
    from fastapi import FastAPI

    from embry0.api.v1 import progression as progression_api

    class Service:
        async def rebuild(self) -> dict[str, Any]:
            return {"action": "rebuilt", "rollup": {"through_day": "2026-03-07"}}

    from embry0.api.deps import _admin_dependency

    app = FastAPI()
    app.include_router(progression_api.router, prefix="/api/v1")
    app.dependency_overrides[_admin_dependency] = lambda: None  # the gate itself is pinned below
    app.state.progression = Service()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/progression/rebuild")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"action": "rebuilt", "rollup": {"through_day": "2026-03-07"}}
        app.state.progression = None
        assert (await client.post("/api/v1/progression/rebuild")).status_code == 503


def test_the_rebuild_route_is_admin_only() -> None:
    from embry0.api.app import create_app
    from embry0.api.deps import _admin_dependency
    from tests.fixtures.routes import app_routes

    route = next(
        (
            r
            for r in app_routes(create_app())
            if r.path == "/api/v1/progression/rebuild" and "POST" in (r.methods or ())
        ),
        None,
    )
    assert route is not None, "POST /api/v1/progression/rebuild is not mounted"
    assert route.dependant is not None
    assert _admin_dependency in {d.call for d in route.dependant.dependencies}, "the rebuild route lost its admin gate"
