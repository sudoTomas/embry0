"""The progression rollup against the migrated schema (#176): a full read's answer, from the closed days stored once.

Random histories are written into Postgres as the clock moves; at every step
the service reading through the rollup tables answers, field for field, what
a service reading every raw row (and the whole journal) answers. Then an
event recorded days late for a folded day is missed until the nightly
rebuild, and the answer says how old the rollup is. Two writers build once.
"""

from __future__ import annotations

import asyncio
import os
import random
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest

from embry0.services.merge_sync.recorder import aligned_status
from embry0.services.progression import ProgressionService
from embry0.services.progression_pack import default_rules
from embry0.services.progression_rollup import ProgressionRollup, closed_through
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.job_delivery import DeliveryWrite, JobDeliveryRepository
from embry0.storage.repositories.progression import Journal, ProgressEvent, ProgressionRepository
from embry0.storage.repositories.progression_rollup import ProgressionRollupRepository
from tests.fixtures.progression_history import _job, _trace
from tests.unit.services.test_progression_rollup import KEYS, PACKS, Packs, differences

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

RULES = default_rules()
AGENTS = ("developer", "review", "qa", "triage", "slopmaster", "pr-reviewer", "my-custom-agent")
REPOS = ("acme/widgets", "acme/lib")
ISSUES = ("iss-1", "iss-2", "iss-3", "iss-4")


class WholeJournal(ProgressionRepository):
    """The reference: every run in the journal, as before the journal was narrowed."""

    async def journal(self, agents: Collection[str] | None = None) -> Journal:
        return await super().journal(None)


class Writer:
    """Writes random rows into the database, each one happening at a time the caller picks."""

    def __init__(self, db: DatabasePool, rng: random.Random) -> None:
        self.db = db
        self.rng = rng
        self.n = 0
        #: Jobs no event comes from (still running), and the jobs of the current batch.
        self.carriers: list[str] = []
        self.batch: list[str] = []
        self.times: list[datetime] = []

    def _when(self, low: datetime, high: datetime) -> datetime:
        recent = [t for t in self.times if low <= t <= high]
        if recent and self.rng.random() < 0.1:
            return self.rng.choice(recent)  # two events at one time
        at = low + (high - low) * self.rng.random()
        self.times.append(at)
        return at

    async def setup(self) -> None:
        for agent in AGENTS:
            await self.db.execute(
                "INSERT INTO agent_definitions (type, description, model) VALUES ($1, 'd', 'm') ON CONFLICT DO NOTHING",
                agent,
            )
        for issue in ISSUES:
            await self.db.execute("INSERT INTO issues (id, title, repo) VALUES ($1, $1, 'acme/widgets')", issue)
        for repo in REPOS:
            await _job(self.db, f"carrier-{repo}", repo=repo, status="running", at=datetime(2025, 12, 1, tzinfo=UTC))
            self.carriers.append(f"carrier-{repo}")

    async def job(self, low: datetime, high: datetime) -> None:
        rng = self.rng
        self.n += 1
        job_id = f"j-{self.n}"
        at = self._when(low, high)
        template = rng.choice((None, None, None, "qa", "context-audit", "slop-cleanup", "onboard"))
        status = rng.choice(("pr_merged", "pr_merged", "completed", "failed"))
        row: dict[str, Any] = {
            "repo": rng.choice(REPOS),
            "status": status,
            "pipeline_template": template,
            "issue_id": rng.choice(ISSUES) if rng.random() < 0.6 else None,
            "resume_attempts": rng.choice((0, 0, 0, 1, 2)),
        }
        if status == "pr_merged" or rng.random() < 0.3:
            row["pr_url"] = f"https://forge/acme/widgets/pull/{self.n}"
        if template == "slop-cleanup":
            row["work_nature"] = {"cleanup": {"added": rng.randint(0, 40), "removed": rng.randint(0, 500)}}
        if template == "context-audit":
            row["result_summary"] = f"Stale: {rng.randint(0, 9)}\nFiles: {rng.randint(1, 5)}\n"
        # A merge's time is its delivery time, five minutes after the job finished.
        ran = rng.uniform(0.1, 3)
        created = at - timedelta(hours=ran, minutes=5 if status == "pr_merged" else 0)
        await _job(self.db, job_id, at=created, ran_hours=ran, **row)
        self.batch.append(job_id)

    async def run(self, low: datetime, high: datetime) -> None:
        rng = self.rng
        self.n += 1
        at = self._when(low, high)
        actions = {rng.choice(KEYS): rng.randint(1, 50) for _ in range(rng.randint(0, 4))}
        error = "ERR_X" if rng.random() < 0.1 else None
        # A run belongs to a job of its own time: a run added to an old job would change that job's
        # resumed event, which names the job's last run, inside the days already folded.
        job_id = rng.choice(self.batch + self.carriers)
        await _trace(self.db, f"trc-{self.n}", job_id, rng.choice(AGENTS), at, actions=actions, error=error)

    async def asked(self, low: datetime, high: datetime) -> None:
        await self.db.execute(
            "INSERT INTO audit_log (action, actor, created_at) VALUES ('issue.input_created', $1, $2)",
            self.rng.choice(AGENTS),
            self._when(low, high),
        )

    async def some(self, low: datetime, high: datetime) -> None:
        self.batch = []
        for _ in range(self.rng.randint(2, 9)):
            await self.job(low, high)
        for _ in range(self.rng.randint(4, 16)):
            await self.run(low, high)
        if self.rng.random() < 0.5:
            await self.asked(low, high)


def _same(actual: Any, expected: Any, context: str) -> None:
    problems = differences(actual, expected)
    assert not problems, f"{context}: the rollup's answer differs from every raw row's:\n" + "\n".join(problems[:12])


@pytest.mark.parametrize("seed", range(4))
async def test_the_rollup_answers_what_every_raw_row_answers(db_with_migrations: DatabasePool, seed: int) -> None:
    db = db_with_migrations
    rng = random.Random(seed)
    writer = Writer(db, rng)
    await writer.setup()
    clock = [datetime(2025, 12, 20, 9, tzinfo=UTC)]
    # Two weeks of history before the first read.
    await writer.some(clock[0] - timedelta(days=14), clock[0])
    for _ in range(4):
        await writer.some(clock[0] - timedelta(days=14), clock[0])
    packs = Packs(RULES)
    service = ProgressionService(
        ProgressionRepository(db),
        packs=packs,
        rollups=ProgressionRollupRepository(db),
        ttl_seconds=0,
        now=lambda: clock[0],
    )
    reference = ProgressionService(WholeJournal(db), packs=packs, rollups=None, ttl_seconds=0)
    used = 0
    for step in range(10):
        context = f"seed {seed}, step {step}, clock {clock[0].isoformat()}"
        if step == 6:
            packs.rules = PACKS["auckland"]  # a pack change: another calendar and other rates
        answer = await service.read()
        _same(answer, await reference.read(), context)
        if step == 6:
            assert answer.rollup is None, "a changed pack is not read from the old pack's rollup"
        await service.settle()
        answer = await service.read()
        used += answer.rollup is not None
        _same(answer, await reference.read(), context + ", after the write")
        stored = [r["pack_hash"] for r in await db.fetch("SELECT pack_hash FROM progression_rollups")]
        assert stored == [packs.rules.hash], f"{context}: the rollups stored are {stored}, not the active pack's alone"
        # Time passes; what happens now is recorded now, after every boundary so far.
        clock[0] += timedelta(hours=rng.uniform(5, 40))
        await writer.some(clock[0] - timedelta(hours=40), clock[0])
    assert used >= 8, f"the rollup answered only {used} of 10 steps"

    # Recorded days late, for a day the rollup holds: missed until the nightly rebuild, and visibly so.
    await service.read()
    await service.settle()
    current = await service.read()
    rollup = current.rollup
    assert rollup is not None
    before_xp = current.characters["developer"].stat_xp.get("coding", 0)
    late_at = datetime.fromisoformat(rollup["boundary"]) - timedelta(days=3)
    await _job(
        db, "j-late", repo="acme/other", status="pr_merged", pr_url="https://forge/acme/other/pull/1", at=late_at
    )
    stale = await service.read()
    assert stale.characters["developer"].stat_xp.get("coding", 0) == before_xp, "a merge recorded late waits a night"
    assert stale.rollup == rollup, "the answer names the rollup it was read from"
    fresh_reference = await reference.read()
    assert fresh_reference.characters["developer"].stat_xp["coding"] > before_xp
    tomorrow = packs.rules.local(clock[0]).date() + timedelta(days=1)
    clock[0] = datetime.combine(tomorrow, time(3, 1), tzinfo=packs.rules.zone)  # past the nightly rebuild
    await service.read()
    await service.settle()
    rebuilt = await service.read()
    assert rebuilt.rollup is not None and rebuilt.rollup["rebuilt_at"] == clock[0].astimezone(UTC).isoformat()
    _same(rebuilt, await reference.read(), f"seed {seed}, after the nightly rebuild")


async def test_events_at_one_time_come_in_one_order_whatever_the_bounds(db_with_migrations: DatabasePool) -> None:
    """Events at one time are ordered by source, then by row: a bounded read keeps a full read's order.

    The rollup folds a day's events in a separate read from the tail's, so a
    tie must not be broken by whatever order Postgres happens to return.
    """
    db = db_with_migrations
    created = datetime(2026, 3, 1, 10, tzinfo=UTC)
    await _job(db, "j-b", status="pr_merged", pr_url="https://forge/acme/widgets/pull/2", at=created)
    await _job(
        db,
        "j-a",
        status="pr_merged",
        pipeline_template="slop-cleanup",
        pr_url="https://forge/acme/widgets/pull/1",
        work_nature={"cleanup": {"added": 1, "removed": 9}},
        at=created,
    )
    merged_at = created + timedelta(minutes=35)  # finished after half an hour, delivered five minutes later
    for trace, calls in (("trc-c", 3), ("trc-a", 1), ("trc-b", 2)):
        await _trace(db, trace, "j-a", "review", merged_at, actions={"read:files": calls})
    repo = ProgressionRepository(db)

    def shape(events: list[ProgressEvent]) -> list[tuple[str, str | None, int | None]]:
        return [(e.source, e.agent_type, (e.skill_actions or {}).get("read:files")) for e in events]

    full = await repo.events()
    assert shape(full) == [
        ("opened", "slopmaster", None),  # j-a before j-b
        ("opened", "developer", None),
        ("run", "review", 1),  # trc-a, trc-b, trc-c
        ("run", "review", 2),
        ("run", "review", 3),
        ("merged", "slopmaster", None),
        ("merged", "developer", None),
        ("cleaned", "slopmaster", None),
    ], shape(full)
    assert shape(await repo.events(since=merged_at)) == shape([e for e in full if e.at >= merged_at])
    assert shape(await repo.events(until=merged_at)) == shape([e for e in full if e.at < merged_at])
    assert shape(await repo.events(since=merged_at, until=merged_at + timedelta(seconds=1))) == shape(full[2:])


class SlowSource(ProgressionRepository):
    """Reads the raw events slowly, so two writers overlap; counts the full reads."""

    reads: list[str] = []

    async def events(self, *, since: datetime | None = None, until: datetime | None = None) -> list[ProgressEvent]:
        SlowSource.reads.append(f"{since} {until}")
        await asyncio.sleep(0.3)
        return await super().events(since=since, until=until)


async def test_two_writers_build_once(db_with_migrations: DatabasePool) -> None:
    """Two replicas finding no rollup at once: the second waits on the lock and finds it built."""
    db = db_with_migrations
    writer = Writer(db, random.Random(7))
    await writer.setup()
    now = datetime(2026, 1, 10, tzinfo=UTC)
    await writer.some(now - timedelta(days=10), now)
    SlowSource.reads = []
    first, second = (
        ProgressionRollup(ProgressionRollupRepository(db), SlowSource(db), now=lambda: now) for _ in range(2)
    )
    done = await asyncio.gather(first.maintain(RULES), second.maintain(RULES), return_exceptions=True)
    actions = sorted(str(d.action) if not isinstance(d, BaseException) else repr(d) for d in done)
    assert actions == ["None", "built"], f"two writers did not build exactly once: {actions}"
    assert len(SlowSource.reads) == 1, f"the history was read {len(SlowSource.reads)} times: {SlowSource.reads}"
    heads = await db.fetch("SELECT pack_hash, boundary FROM progression_rollups")
    assert [(r["pack_hash"], r["boundary"]) for r in heads] == [(RULES.hash, closed_through(now, RULES)[1])]


def _url() -> str:
    return os.environ.get("TEST_DATABASE_URL", "postgresql://embry0:embry0@localhost:5432/embry0_test")


async def test_concurrent_rebuilds_on_a_small_pool_all_finish(db_with_migrations: DatabasePool) -> None:
    """Each write needs two connections: the one holding the lock and one to read. Waiting writers must hold none."""
    writer = Writer(db_with_migrations, random.Random(5))
    await writer.setup()
    now = datetime(2026, 1, 10, tzinfo=UTC)
    await writer.some(now - timedelta(days=10), now)
    small = DatabasePool(_url(), min_size=1, max_size=3)
    await small.connect()
    calls: list[asyncio.Task[dict[str, Any]]] = []
    try:
        service = ProgressionService(
            ProgressionRepository(small),
            packs=Packs(RULES),
            rollups=ProgressionRollupRepository(small),
            now=lambda: now,
        )
        calls = [asyncio.create_task(service.rebuild()) for _ in range(6)]
        done, pending = await asyncio.wait(calls, timeout=30)
        assert not pending, f"{len(pending)} of 6 concurrent rebuilds hung on a pool of 3 connections"
        actions = sorted(str(call.result()["action"]) for call in done)
        assert actions == ["None"] * 5 + ["built"], f"the rollup was not built exactly once: {actions}"
    finally:
        for call in calls:
            call.cancel()
        assert small.pool is not None
        small.pool.terminate()


async def test_a_job_idle_for_days_with_a_pull_request_counts_once(db_with_migrations: DatabasePool) -> None:
    """A job's time is never the time its row was last touched: that moves, and would count it in two places."""
    db = db_with_migrations
    await db.execute(
        "INSERT INTO agent_definitions (type, description, model) VALUES ('developer', 'd', 'm') ON CONFLICT DO NOTHING"
    )
    clock = [datetime(2026, 3, 10, 13, tzinfo=UTC)]
    # A follow-up job opened its pull request, then waited for a person: not finished, untouched for five days.
    await db.execute(
        """
        INSERT INTO jobs (job_id, repo, task, status, pipeline_template, pr_url, created_at, started_at, updated_at,
                          budget_overrun_usd)
        VALUES ('j-paused', 'acme/widgets', 't', 'paused', 'pr-followup', 'https://forge/acme/widgets/pull/9',
                $1, $1, $2, 0)
        """,
        clock[0] - timedelta(days=6),
        clock[0] - timedelta(days=5),
    )
    packs = Packs(RULES)
    service = ProgressionService(
        ProgressionRepository(db),
        packs=packs,
        rollups=ProgressionRollupRepository(db),
        ttl_seconds=0,
        now=lambda: clock[0],
    )
    reference = ProgressionService(WholeJournal(db), packs=packs, rollups=None, ttl_seconds=0)
    await service.read()
    await service.settle()
    # Something touches the row (a sweep, a reminder): its update time moves into the recent days.
    await db.execute("UPDATE jobs SET updated_at = $1 WHERE job_id = 'j-paused'", clock[0] - timedelta(hours=1))
    _same(await service.read(), await reference.read(), "after the paused job's row was touched")
    # It finishes: its pull request counts now, once.
    finished = clock[0] - timedelta(minutes=30)
    await db.execute(
        "UPDATE jobs SET status = 'completed', finished_at = $1, updated_at = $1 WHERE job_id = 'j-paused'", finished
    )
    answer = await service.read()
    _same(answer, await reference.read(), "after the job finished")
    opened = RULES.outcomes["opened"]
    assert answer.characters["developer"].stat_xp.get(opened.skill) == opened.xp, (
        f"the opened pull request counts once, when the job finished: {answer.characters['developer'].stat_xp}"
    )


async def test_a_read_is_one_snapshot_even_when_a_write_commits_between_its_queries(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The head, the sums and the milestones are three queries: they must come from one snapshot."""
    import embry0.storage.repositories.progression_rollup as rollup_repo

    db = db_with_migrations
    writer = Writer(db, random.Random(11))
    await writer.setup()
    now = datetime(2026, 1, 10, tzinfo=UTC)
    await writer.some(now - timedelta(days=10), now)
    store = ProgressionRollupRepository(db)
    await ProgressionRollup(store, ProgressionRepository(db), now=lambda: now).maintain(RULES)
    before = await store.load(RULES.hash)
    assert before is not None and before.xp and before.milestones
    real_load = rollup_repo._load

    async def load_with_a_write_between(conn: Any, pack_hash: str) -> Any:
        class Between:
            """The connection, committing a rebuild that empties the rollup after the head is read."""

            wrote = False

            async def fetchrow(self, *args: Any) -> Any:
                return await conn.fetchrow(*args)

            async def fetch(self, *args: Any) -> Any:
                if not Between.wrote:
                    Between.wrote = True
                    async with store.writing() as w:
                        await w.replace(replace(before.head, built_at=now + timedelta(hours=1)), [], [])
                return await conn.fetch(*args)

        return await real_load(Between(), pack_hash)

    monkeypatch.setattr(rollup_repo, "_load", load_with_a_write_between)
    during = await store.load(RULES.hash)
    assert during == before, "a read mixed the rollup's head from before a write with rows from after it"
    monkeypatch.setattr(rollup_repo, "_load", real_load)
    after = await store.load(RULES.hash)
    assert after is not None and after.xp == {} and after.head.built_at == now + timedelta(hours=1)


async def test_a_reader_never_sees_a_rollup_half_written(db_with_migrations: DatabasePool) -> None:
    """A read during a rebuild sees the rollup before it, whole; the rebuild's rows appear at once."""
    db = db_with_migrations
    writer = Writer(db, random.Random(3))
    await writer.setup()
    now = datetime(2026, 1, 10, tzinfo=UTC)
    await writer.some(now - timedelta(days=10), now)
    store = ProgressionRollupRepository(db)
    await ProgressionRollup(store, ProgressionRepository(db), now=lambda: now).maintain(RULES)
    before = await store.load(RULES.hash)
    assert before is not None and before.milestones
    async with store.writing() as w:
        await w.replace(before.head, [], [])  # emptied, not yet committed
        during = await store.load(RULES.hash)
        assert during == before, "a reader saw the rollup while it was being rewritten"
    after = await store.load(RULES.hash)
    assert after is not None and after.milestones == [] and after.xp == {}


# ---- a job keeps what it earned when its pull request merges after its day was folded (#210) ----

#: What a finished job of each template earns: its character and skill, an achievement it moves, and diary task kinds.
EARNED = {
    "context-audit": ("context-auditor", "documentation", "weeded_garden", ("audit", "clean_audit")),
    "qa": ("qa", "quality_assurance", "potion_master", ("run_qa", "pass_qa")),
}


def _earned(progression: Any, template: str) -> tuple[int, float, dict[str, Any]]:
    """The job's experience, its achievement's value, and when each of its diary tasks was done."""
    character, skill, achievement, kinds = EARNED[template]
    xp = progression.characters[character].stat_xp.get(skill, 0) if character in progression.characters else 0
    value = next(a.value for a in progression.achievements if a.achievement.entry.id == achievement)
    diary = next(d for d in progression.diaries if d.repo == "acme/widgets")
    done = {
        entry.kind: task.done_at
        for tier in RULES.pack.diaries.tiers
        for entry, task in zip(tier.tasks, diary.tier(tier.id).tasks, strict=True)
        if entry.kind in kinds
    }
    return xp, value, done


@pytest.mark.parametrize("template", sorted(EARNED))
async def test_a_job_keeps_what_it_earned_when_its_pull_request_merges_after_its_day_was_folded(
    db_with_migrations: DatabasePool, template: str
) -> None:
    """A finished audit or QA run whose pull request merges reads the same from the rollup, a full read and a rebuild."""
    db = db_with_migrations
    for agent in ("developer", "qa", "context-auditor"):
        await db.execute(
            "INSERT INTO agent_definitions (type, description, model) VALUES ($1, 'd', 'm') ON CONFLICT DO NOTHING",
            agent,
        )
    finished = datetime(2026, 3, 2, 12, tzinfo=UTC)
    await _job(
        db,
        "j-done",
        status="completed",
        pipeline_template=template,
        result_summary="Stale: 0\nFiles: 2\n" if template == "context-audit" else None,
        pr_url="https://forge/acme/widgets/pull/1",
        delivery_state="pr_open",
        at=finished - timedelta(hours=1),
        ran_hours=1,
    )
    clock = [finished + timedelta(days=5)]
    packs = Packs(RULES)
    service = ProgressionService(
        ProgressionRepository(db),
        packs=packs,
        rollups=ProgressionRollupRepository(db),
        ttl_seconds=0,
        now=lambda: clock[0],
    )
    reference = ProgressionService(WholeJournal(db), packs=packs, rollups=None, ttl_seconds=0)
    await service.read()
    await service.settle()
    folded = await service.read()
    assert folded.rollup is not None and datetime.fromisoformat(folded.rollup["boundary"]) > finished, folded.rollup
    xp, value, done = _earned(folded, template)
    assert xp > 0 and value == 1 and None not in done.values(), f"the finished job earned nothing: {xp, value, done}"

    # Its pull request merges now: merge sync records the delivery, and the status follows it to pr_merged.
    def merged(row: dict[str, Any]) -> DeliveryWrite:
        return DeliveryWrite(
            delivery_state="merged", delivery_state_at=clock[0], status=aligned_status(row["status"], "merged")
        )

    assert await JobDeliveryRepository(db).apply("j-done", merged) is not None
    assert await db.fetchval("SELECT status FROM jobs WHERE job_id = 'j-done'") == "pr_merged"
    full = await reference.read()
    full_xp, full_value, full_done = _earned(full, template)
    assert full_xp == xp, f"a full read took back the {template} job's experience when its pull request merged"
    assert full_value == value, f"a full read took back the {template} job's achievement progress"
    assert None not in full_done.values(), f"a full read took back the {template} job's diary tasks: {full_done}"
    through_rollup = await service.read()
    assert through_rollup.rollup is not None, "the read after the merge is the rollup's"
    _same(through_rollup, full, f"the {template} job's pull request merged after its day was folded")

    # The nightly rebuild folds every raw row again: the experience stays.
    tomorrow = packs.rules.local(clock[0]).date() + timedelta(days=1)
    clock[0] = datetime.combine(tomorrow, time(3, 1), tzinfo=packs.rules.zone)
    await service.read()
    await service.settle()
    rebuilt = await service.read()
    assert rebuilt.rollup is not None and rebuilt.rollup["rebuilt_at"] == clock[0].astimezone(UTC).isoformat()
    assert _earned(rebuilt, template)[0] == xp, f"the nightly rebuild took back the {template} job's experience"
    _same(rebuilt, await reference.read(), f"the {template} job's pull request merged, after the nightly rebuild")


# ---- rows at one time read the same, however Postgres stores them (#443) ----

TIED = datetime(2026, 3, 1, 10, tzinfo=UTC)


@dataclass(frozen=True)
class _Metric:
    """One metric of the facts query, filed under its fragment's name."""

    fragment: str
    params: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return self.fragment


async def _write_tied(db: DatabasePool, order: str) -> None:
    """Two jobs merged on one issue of one repo, two runs of a resumed job, and runs in two other repos, at one time.

    ``order`` is the order the rows are written in, which is the order
    Postgres stores them in: ``"ab"`` or ``"ba"``.
    """
    for name in order:
        await _job(
            db,
            f"j-{name}",
            status="pr_merged",
            issue_id="iss-2",
            pipeline_template="map-areas",
            pr_url=f"https://forge/acme/widgets/pull/{name}",
            dispatched_by=f"user-{name}",
            at=TIED,
        )
    await _job(db, "j-resumed", status="completed", resume_attempts=1, at=TIED)
    for name in order:
        await _trace(db, f"trc-{name}", "j-resumed", {"a": "review", "b": "qa"}[name], TIED)
    # A run of one agent in each of two other repos.
    for name in order:
        await _job(db, f"j-{name}-elsewhere", repo=f"acme/{name}", at=TIED)
        await _trace(db, f"trc-{name}-elsewhere", f"j-{name}-elsewhere", "developer", TIED)


async def _fresh_heap(db: DatabasePool) -> None:
    """Empty the tables the tied rows are written to, with a new heap each.

    Rows are stored in the order they are written only into an empty heap:
    one the database emptied before (an earlier test's rows, the first write
    of this one) has free space a new row can be put in, ahead of a row
    written before it.
    """
    await db.execute("TRUNCATE traces, jobs CASCADE")


async def _tied_setup(db: DatabasePool) -> None:
    for agent in ("developer", "review", "qa"):
        await db.execute(
            "INSERT INTO agent_definitions (type, description, model) VALUES ($1, 'd', 'm') ON CONFLICT DO NOTHING",
            agent,
        )
    await db.execute("INSERT INTO issues (id, title, repo) VALUES ('iss-1', 'Parent', 'acme/widgets')")
    await db.execute(
        "INSERT INTO issues (id, title, repo, parent_issue_id) VALUES ('iss-2', 'Child', 'acme/widgets', 'iss-1')"
    )


async def _urls(repo: ProgressionRepository, metric: _Metric) -> list[str | None]:
    return [fact.url for fact in await repo.facts([metric], "UTC")]


async def _resumed_by(repo: ProgressionRepository) -> list[str | None]:
    return [event.agent_type for event in await repo.events() if event.source == "resumed"]


async def _journal_jobs(repo: ProgressionRepository) -> list[tuple[str, str | None]]:
    return [(job.repo, job.dispatched_by) for job in (await repo.journal()).jobs]


async def _journal_runs(repo: ProgressionRepository) -> list[tuple[str, str]]:
    return [(run.agent_type, run.repo) for run in (await repo.journal()).runs]


#: Each read that lists rows at one time, or keeps one of them: the facts of a metric in order (a metric's
#: evidence is its last), the one row a DISTINCT ON keeps, the agent a resumed job credits, the journal.
TIED_READS: dict[str, Callable[[ProgressionRepository], Awaitable[list[Any]]]] = {
    "merged, in order": lambda repo: _urls(repo, _Metric("merged")),
    "repo_hopper keeps one": lambda repo: _urls(repo, _Metric("repo_hopper")),
    "issue_done keeps one": lambda repo: _urls(repo, _Metric("issue_done")),
    "side_quest keeps one": lambda repo: _urls(repo, _Metric("side_quest")),
    "areas_mapped keeps one": lambda repo: _urls(repo, _Metric("areas_mapped")),
    "merged_on_attempt keeps one": lambda repo: _urls(repo, _Metric("merged_on_attempt", {"min_attempts": 1})),
    "the resumed job's last run": _resumed_by,
    "the journal's jobs": _journal_jobs,
    "the journal's runs": _journal_runs,
}


#: Each pair of rows at one time that _write_tied writes: its table, its key column, the a row and the b row.
TIED_PAIRS = (
    ("jobs", "job_id", "j-a", "j-b"),
    ("traces", "trace_id", "trc-a", "trc-b"),
    ("jobs", "job_id", "j-a-elsewhere", "j-b-elsewhere"),
    ("traces", "trace_id", "trc-a-elsewhere", "trc-b-elsewhere"),
)


async def _stored_first(db: DatabasePool, pairs: tuple[tuple[str, str, str, str], ...] = TIED_PAIRS) -> list[str]:
    """Which row of each pair Postgres stores first ("a" or "b"), by its place on disk (``ctid``): the order a scan
    meets them in. A test of what a different order reads measures nothing unless this differs."""
    first: list[str] = []
    for table, key, a, b in pairs:
        rows = await db.fetch(f"SELECT {key} FROM {table} WHERE {key} = ANY($1::text[]) ORDER BY ctid", [a, b])
        assert len(rows) == 2, f"{table} holds {len(rows)} of {a} and {b}"
        first.append("a" if rows[0][key] == a else "b")
    return first


@pytest.mark.parametrize("read", sorted(TIED_READS))
async def test_rows_at_one_time_read_the_same_whatever_order_they_were_stored_in(
    db_with_migrations: DatabasePool, read: str
) -> None:
    """The rollup folds a day in one read and a full read reads it again: both must keep the same of tied rows."""
    db = db_with_migrations
    await _tied_setup(db)
    repo = ProgressionRepository(db)
    answers: dict[str, list[Any]] = {}
    stored: dict[str, list[str]] = {}
    for order in ("ab", "ba"):
        await _fresh_heap(db)
        await _write_tied(db, order)
        stored[order] = await _stored_first(db)
        answers[order] = await TIED_READS[read](repo)
    assert stored == {"ab": ["a"] * len(TIED_PAIRS), "ba": ["b"] * len(TIED_PAIRS)}, (
        f"the rows were not stored in the order they were written, so the two reads saw one order: {stored}"
    )
    assert answers["ab"] == answers["ba"], (
        f"{read}: the same rows stored in another order read {answers['ba']}, not {answers['ab']}"
    )


async def _store_again(db: DatabasePool, table: str, key: str, value: str) -> None:
    """Delete the row and write it back unchanged: Postgres now stores it after the rows written since."""
    await db.execute(
        f"WITH gone AS (DELETE FROM {table} WHERE {key} = $1 RETURNING *) INSERT INTO {table} SELECT * FROM gone", value
    )


async def test_the_rollup_and_a_full_read_keep_the_same_of_rows_at_one_time(db_with_migrations: DatabasePool) -> None:
    """#443: the same rows answer the same, through the rollup or not, however Postgres happens to store them."""
    db = db_with_migrations
    await _tied_setup(db)
    await _fresh_heap(db)
    await _write_tied(db, "ab")
    clock = [TIED + timedelta(days=5)]
    packs = Packs(RULES)
    service = ProgressionService(
        ProgressionRepository(db),
        packs=packs,
        rollups=ProgressionRollupRepository(db),
        ttl_seconds=0,
        now=lambda: clock[0],
    )
    reference = ProgressionService(WholeJournal(db), packs=packs, rollups=None, ttl_seconds=0)
    await service.read()
    await service.settle()
    folded = await service.read()
    assert folded.rollup is not None and datetime.fromisoformat(folded.rollup["boundary"]) > TIED, folded.rollup

    # Nothing changes but where Postgres keeps the first row of each pair.
    moved = TIED_PAIRS[:2]
    assert await _stored_first(db, moved) == ["a", "a"], "the rows were written a first"
    await _store_again(db, "jobs", "job_id", "j-a")
    await _store_again(db, "traces", "trace_id", "trc-a")
    assert await _stored_first(db, moved) == ["b", "b"], (
        "storing a again must put it after b on disk, or the reads below compare one order with itself"
    )

    _same(folded, await reference.read(), "the same rows, stored again")
    _same(await service.read(), await reference.read(), "the rollup's folded day, after its rows were stored again")
