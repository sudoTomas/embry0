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
from collections.abc import Collection
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest

from embry0.services.progression import ProgressionService
from embry0.services.progression_pack import default_rules
from embry0.services.progression_rollup import ProgressionRollup, closed_through
from embry0.storage.database import DatabasePool
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
