"""The progression's computations in worker processes (``embry0/services/progression_workers.py``).

On a real database:

- a service with fold workers answers, field for field, what one computing on
  its event loop answers over the same rows: the deployment's and a set of
  repositories', through each one's rollup (built by the workers) and from
  every raw row, and a pack's preview;
- a fold of a history big enough to hold the loop for a while, computed on
  the loop, holds it; in a worker it does not;
- the sets of repositories never take the process kept for the deployment's
  computations;
- a worker process that dies is replaced, and closing the workers stops
  their processes.
"""

from __future__ import annotations

import asyncio
import os
import random
import signal
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from embry0.services.progression import ProgressionService
from embry0.services.progression_rollup import Scope
from embry0.services.progression_workers import FoldTimeout, FoldWorkers, fold_processes
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.progression import ProgressionRepository
from embry0.storage.repositories.progression_rollup import ProgressionRollupRepository
from tests.unit.services.test_fold_worker_processes import _gone
from tests.unit.services.test_progression_rollup import Packs, differences
from tests.unit.storage.test_progression_rollup_repo import RULES, Writer

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

CLOCK = datetime(2025, 12, 20, 9, tzinfo=UTC)
#: The services' wall clock, which a test may move.
NOW = [CLOCK]
SCOPE = ("acme/widgets",)


def _service(db: DatabasePool, workers: FoldWorkers | None) -> ProgressionService:
    return ProgressionService(
        ProgressionRepository(db),
        packs=Packs(RULES),
        rollups=ProgressionRollupRepository(db),
        ttl_seconds=0,
        now=lambda: NOW[0],
        workers=workers,
    )


@pytest.fixture(autouse=True)
def _clock() -> None:
    NOW[0] = CLOCK


@pytest.fixture
async def workers(db_with_migrations: DatabasePool) -> AsyncIterator[FoldWorkers]:
    found = FoldWorkers(db_with_migrations._url, processes=2, json_logs=False)  # noqa: SLF001 - the test database's
    try:
        yield found
    finally:
        await found.aclose(timeout=10)


def _same(actual: object, expected: object, context: str) -> None:
    problems = differences(actual, expected)  # type: ignore[arg-type]
    assert not problems, f"{context}:\n" + "\n".join(problems[:12])


async def test_a_worker_answers_what_the_loop_answers(db_with_migrations: DatabasePool, workers: FoldWorkers) -> None:
    db = db_with_migrations
    writer = Writer(db, random.Random(3))
    await writer.setup()
    for _ in range(5):
        await writer.some(CLOCK - timedelta(days=14), CLOCK)
    here, there = _service(db, None), _service(db, workers)
    for repos in (None, list(SCOPE)):
        raw = await here.compute(RULES, repos)
        _same(await there.compute(RULES, repos), raw, f"the preview of {repos}")
        _same(await there.read(repos), raw, f"the first read of {repos}, from every raw row")
        await there.settle()
        through = await there.read(repos)
        assert through.rollup is not None, f"the workers built the rollup of {repos}"
        _same(through, await here.read(repos), f"the read of {repos} through its rollup")
        _same(through, raw, f"the read of {repos} through its rollup against every raw row")
    NOW[0] = CLOCK + timedelta(minutes=1)
    done = await there.rebuild()
    assert done["action"] == "rebuilt", f"a worker rebuilt the deployment's rollup: {done}"
    _same(await there.read(), await here.compute(RULES), "the deployment's read after the rebuild")


async def _bulk(db: DatabasePool, jobs: int, runs: int) -> None:
    """A history of ``jobs`` jobs over two repositories and a year, with ``runs`` runs that each made tool calls."""
    await db.execute(
        f"""INSERT INTO jobs (job_id, repo, task, status, created_at, started_at, finished_at, updated_at,
                              delivery_state, delivery_state_at, pr_url)
        SELECT 'bj' || g, CASE WHEN g % 2 = 0 THEN 'acme/widgets' ELSE 'acme/lib' END, 't',
               (ARRAY['pr_merged', 'completed', 'failed', 'completed'])[1 + g % 4],
               TIMESTAMPTZ '2025-12-19' - (g % 365 || ' days')::interval,
               TIMESTAMPTZ '2025-12-19' - (g % 365 || ' days')::interval,
               TIMESTAMPTZ '2025-12-19' - (g % 365 || ' days')::interval + interval '30 seconds',
               TIMESTAMPTZ '2025-12-19' - (g % 365 || ' days')::interval + interval '30 seconds',
               CASE WHEN g % 4 = 0 THEN 'merged' ELSE 'none' END,
               CASE WHEN g % 4 = 0 THEN TIMESTAMPTZ '2025-12-19' - (g % 365 || ' days')::interval END,
               CASE WHEN g % 4 IN (0, 1) THEN 'https://github.com/acme/x/pull/' || g END
        FROM generate_series(1, {jobs}) g"""
    )
    await db.execute(
        f"""INSERT INTO traces (trace_id, job_id, agent_type, model, result, tools_called, skill_actions, created_at)
        SELECT 'bt' || g, 'bj' || (1 + g % {jobs}), (ARRAY['developer', 'review', 'qa', 'triage'])[1 + g % 4], 'm',
               'success', '{{"Edit": 3, "Bash": 2, "Read": 4}}'::jsonb, '{{"edit:code": 2}}'::jsonb,
               TIMESTAMPTZ '2025-12-19' - ((1 + g % {jobs}) % 365 || ' days')::interval + (g % 30 || ' seconds')::interval
        FROM generate_series(1, {runs}) g"""
    )
    await db.execute("ANALYZE")


async def _held(work: Callable[[], Awaitable[Any]]) -> tuple[float, float]:
    """How long ``work`` took, and the longest the event loop went without running a tick meanwhile."""
    longest = 0.0
    done = False

    async def tick() -> None:
        nonlocal longest
        last = time.monotonic()
        while not done:
            await asyncio.sleep(0.005)
            now = time.monotonic()
            longest = max(longest, now - last)
            last = now

    ticker = asyncio.create_task(tick())
    started = time.monotonic()
    await work()
    took = time.monotonic() - started
    done = True
    await ticker
    return took, longest


async def test_a_fold_in_a_worker_does_not_hold_the_event_loop(
    db_with_migrations: DatabasePool, workers: FoldWorkers
) -> None:
    db = db_with_migrations
    await _bulk(db, 20_000, 150_000)
    here, there = _service(db, None), _service(db, workers)
    await there.compute(RULES, list(SCOPE))  # the process starts and imports, which is not the fold
    took_here, held_here = await _held(lambda: here.compute(RULES))
    assert held_here > 0.25, (
        f"on the loop the fold held it {held_here:.3f}s of {took_here:.2f}s: too small a history to measure"
    )
    for name, work in (
        ("a read's fold", lambda: there.compute(RULES)),
        ("a full build of the rollup", lambda: there.rebuild()),
    ):
        took_there, held_there = await _held(work)
        assert held_there < max(0.1, held_here / 5), (
            f"{name} in a worker held the loop {held_there:.3f}s of {took_there:.2f}s; a fold on it, {held_here:.3f}s"
        )


async def test_a_worker_that_dies_is_replaced_and_closing_stops_them(
    db_with_migrations: DatabasePool, workers: FoldWorkers
) -> None:
    db = db_with_migrations
    writer = Writer(db, random.Random(5))
    await writer.setup()
    await writer.some(CLOCK - timedelta(days=3), CLOCK)
    service = _service(db, workers)
    first = await service.compute(RULES)
    processes = [p for slot in workers._slots for p in slot.processes()]  # noqa: SLF001
    assert processes and all(p.is_alive() for p in processes)
    for process in processes:
        assert process.pid is not None
        os.kill(process.pid, signal.SIGKILL)
        process.join(5)
    _same(await service.compute(RULES), first, "the computation once its process died")
    replaced = [p for slot in workers._slots for p in slot.processes()]  # noqa: SLF001
    assert replaced and not {p.pid for p in replaced} & {p.pid for p in processes}, "new processes"
    await service.aclose()
    assert all(_gone(p.pid) for p in replaced if p.pid is not None), "closing the service stops its processes"


async def test_production_computes_the_progression_in_workers() -> None:
    """The lifespan builds the app's state with fold workers, and the state builder hands them to the service; a
    test app, built without them, computes on its loop."""
    import inspect
    from unittest.mock import MagicMock

    from embry0.api import app as app_module

    assert "fold_workers=FOLD_WORKERS" in inspect.getsource(app_module.lifespan), (
        "the production lifespan passes fold_workers=FOLD_WORKERS to _init_app_state"
    )
    for fold_workers in (3, 0):
        app = app_module.create_app()
        app.state.secrets_provider = MagicMock()
        await app_module._init_app_state(  # noqa: SLF001
            app=app, db=MagicMock(), database_url="postgresql://test/test", fold_workers=fold_workers
        )
        found = app.state.progression._workers  # noqa: SLF001
        if fold_workers:
            assert isinstance(found, FoldWorkers), "the service computes in workers"
            assert (found._url, found._processes) == ("postgresql://test/test", fold_processes(3))  # noqa: SLF001
        else:
            assert found is None, "a test app computes on its loop"


async def test_a_stopped_worker_fails_its_read_in_time_and_the_next_read_answers(
    db_with_migrations: DatabasePool,
) -> None:
    """A read whose process does not answer fails at its limit, and the scope's lock goes with it."""
    db = db_with_migrations
    writer = Writer(db, random.Random(7))
    await writer.setup()
    await writer.some(CLOCK - timedelta(days=3), CLOCK)
    workers = FoldWorkers(db._url, processes=2, json_logs=False, read_limit=3.0)  # noqa: SLF001
    service = _service(db, workers)
    try:
        await service.read(list(SCOPE))
        await service.settle()  # the scope's rollup, built: the process stopped below runs nothing else
        expected = await service.read(list(SCOPE))
        stopped = workers._slots[1].processes()[0].pid  # noqa: SLF001 - the sets' process
        assert stopped is not None
        os.kill(stopped, signal.SIGSTOP)
        started = time.monotonic()
        with pytest.raises(FoldTimeout):
            await asyncio.wait_for(service.read(list(SCOPE)), 20)
        assert time.monotonic() - started < 10, "the read failed at its limit"
        _same(await asyncio.wait_for(service.read(list(SCOPE)), 30), expected, "the next read, on a new process")
    finally:
        await service.aclose()


async def test_the_production_wiring_serves_the_progression_routes_from_workers(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the app _init_app_state builds with fold workers, as the lifespan does: each progression route
    answers what the same app answers computing on its loop, and a read past its limit is a 503."""
    from tests.fixtures.repository_writes import call, repository_writes_app

    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch, fold_workers=2) as (app, keys, _values):
        writer = Writer(db, random.Random(11))
        await writer.setup()
        await writer.some(CLOCK - timedelta(days=3), CLOCK)
        service = app.state.progression
        workers = service._workers  # noqa: SLF001
        assert isinstance(workers, FoldWorkers)
        routes = ("/api/v1/progression", "/api/v1/world", "/api/v1/world/characters/developer")

        async def answers() -> dict[tuple[str, str], Any]:
            found = {}
            for key in (keys.open, keys.limited):
                for route in routes:
                    service._cached.clear()  # noqa: SLF001
                    response = await call(app, "GET", route, key)
                    assert response.status_code == 200, (route, response.text)
                    found[(route, "open" if key == keys.open else "limited")] = _timeless(response.json())
            return found

        await answers()
        await service.settle()  # the rollups the first reads asked for, built: the answers below read them
        through_workers = await answers()
        used = {p.pid for slot in workers._slots for p in slot.processes() if p.is_alive()}  # noqa: SLF001
        assert len(used) == 2, f"the deployment's and a set's computations ran in two worker processes: {used}"
        service._workers = None  # noqa: SLF001 - the same app, computing on its loop
        on_the_loop = await answers()
        moved = sorted(
            f"{route} {who} {field}"
            for (route, who), answer in on_the_loop.items()
            for field in answer
            if answer[field] != through_workers[(route, who)].get(field)
        )
        assert not moved, f"the routes answer otherwise from the workers: {moved}"
        service._workers = workers  # noqa: SLF001

        workers._limits["read"] = 2.0  # noqa: SLF001
        deployment = workers._slots[0].processes()[0].pid  # noqa: SLF001
        assert deployment is not None
        os.kill(deployment, signal.SIGSTOP)
        service._cached.clear()  # noqa: SLF001
        response = await asyncio.wait_for(call(app, "GET", "/api/v1/progression", keys.open), 20)
        assert response.status_code == 503 and response.headers.get("retry-after") == "30", response.text
        assert (await call(app, "GET", "/api/v1/progression", keys.open)).status_code == 200, "on a new process"


def _timeless(value: Any) -> Any:
    """``value`` without the instants it was computed and answered at."""
    if isinstance(value, dict):
        return {k: _timeless(v) for k, v in value.items() if k not in {"computed_at", "generated_at"}}
    if isinstance(value, list):
        return [_timeless(v) for v in value]
    return value


class _Workers:
    """Stands in for FoldWorkers: computes on the loop, over the database, but each read listed in ``late`` runs past
    its limit (``FoldTimeout``), after waiting for ``hold`` when one is given."""

    def __init__(self, db: DatabasePool, late: int = 1, hold: asyncio.Event | None = None) -> None:
        from embry0.services.progression import Computation
        from embry0.services.progression_rollup import ProgressionRollup

        repository = ProgressionRepository(db)
        rollup = ProgressionRollup(ProgressionRollupRepository(db), repository, now=lambda: NOW[0])
        self.computation = Computation(repository, rollup)
        self.late = late
        self.hold = hold
        self.reads = 0

    async def progression(
        self, rules: Any, scope: Scope, *, through_rollup: bool, rollup: bool, now: datetime
    ) -> tuple[Any, bool]:
        self.reads += 1
        if self.hold is not None:
            await self.hold.wait()
        if self.late:
            self.late -= 1
            raise FoldTimeout("past its limit")
        return await self.computation.progression(rules, scope, through_rollup=through_rollup)

    async def maintain(self, rules: Any, scope: Scope, *, force_since: datetime | None, now: datetime) -> Any:
        return await self.computation.maintain(rules, scope, force_since=force_since)

    async def aclose(self, *, timeout: float) -> None:
        return None


async def test_a_scope_with_no_rollup_has_it_built_before_its_read_is_sent(db_with_migrations: DatabasePool) -> None:
    """The build starts while the first read still computes, so a read that never finishes cannot keep it from
    the rollup: the next read answers from it."""
    db = db_with_migrations
    writer = Writer(db, random.Random(13))
    await writer.setup()
    for _ in range(3):
        await writer.some(CLOCK - timedelta(days=14), CLOCK)
    hold = asyncio.Event()
    stand_in = _Workers(db, late=1, hold=hold)
    service = _service(db, stand_in)  # type: ignore[arg-type]
    first = asyncio.create_task(service.read(list(SCOPE)))
    for _ in range(500):
        if stand_in.reads or first.done():
            break
        await asyncio.sleep(0.01)
    assert stand_in.reads == 1, "the read was sent to the workers"
    assert Scope.of(SCOPE).key in service._tasks, "the build was scheduled before the read finished"  # noqa: SLF001
    await service.settle()
    hold.set()
    with pytest.raises(FoldTimeout):
        await first
    answer = await service.read(list(SCOPE))
    assert answer.rollup is not None, "the read after it answers from the rollup the build made"
    await service.settle()


async def test_a_read_past_its_limit_schedules_the_write_its_rollup_wants(db_with_migrations: DatabasePool) -> None:
    """A scope whose rollup exists but wants advancing, read by a read that runs past its limit: the write is
    scheduled as it fails, and the next read answers from the rollup it advanced."""
    db = db_with_migrations
    writer = Writer(db, random.Random(17))
    await writer.setup()
    for _ in range(3):
        await writer.some(CLOCK - timedelta(days=14), CLOCK)
    stand_in = _Workers(db, late=0)
    service = _service(db, stand_in)  # type: ignore[arg-type]
    await service.read(list(SCOPE))
    await service.settle()
    built = await service.read(list(SCOPE))
    assert built.rollup is not None
    NOW[0] = CLOCK + timedelta(days=3)  # days closed since: the rollup wants advancing
    stand_in.late = 1
    with pytest.raises(FoldTimeout):
        await service.read(list(SCOPE))
    assert Scope.of(SCOPE).key in service._tasks, "the read that failed scheduled the write"  # noqa: SLF001
    await service.settle()
    after = await service.read(list(SCOPE))
    assert after.rollup is not None and after.rollup["through_day"] > built.rollup["through_day"], (
        built.rollup,
        after.rollup,
    )
    await service.settle()


async def test_a_cold_read_past_its_limit_leaves_the_build_running_in_the_workers(
    db_with_migrations: DatabasePool,
) -> None:
    """In real worker processes: a first read too slow for its limit fails, the build goes on without anyone asking
    for it again, and a read after it answers from the rollup."""
    db = db_with_migrations
    writer = Writer(db, random.Random(19))
    await writer.setup()
    for _ in range(3):
        await writer.some(CLOCK - timedelta(days=14), CLOCK)
    workers = FoldWorkers(db._url, processes=2, json_logs=False, read_limit=0.05)  # noqa: SLF001
    service = _service(db, workers)
    try:
        with pytest.raises(FoldTimeout):
            await asyncio.wait_for(service.read(), 20)  # a new process alone takes longer than 0.05 s to start
        await asyncio.wait_for(service.settle(), 60)
        assert await db.fetchval("SELECT count(*) FROM progression_scope_rollups WHERE scope = ''") == 1, (
            "the build ran after the read failed"
        )
        workers._limits["read"] = 30.0  # noqa: SLF001
        answer = await asyncio.wait_for(service.read(), 40)
        assert answer.rollup is not None, "and the read after it answers from the rollup"
    finally:
        await service.aclose()


async def test_a_worker_s_read_that_finds_its_rollup_behind_schedules_the_write(
    db_with_migrations: DatabasePool,
) -> None:
    """A read in a worker that answers from a rollup a day behind says so, and the service schedules the write."""
    db = db_with_migrations
    writer = Writer(db, random.Random(23))
    await writer.setup()
    for _ in range(3):
        await writer.some(CLOCK - timedelta(days=14), CLOCK)
    stand_in = _Workers(db, late=0)
    service = _service(db, stand_in)  # type: ignore[arg-type]
    await service.read(list(SCOPE))
    await service.settle()
    built = await service.read(list(SCOPE))
    assert built.rollup is not None
    NOW[0] = CLOCK + timedelta(days=3)
    behind = await service.read(list(SCOPE))
    assert behind.rollup == built.rollup, "the read answered from the rollup as it stood"
    assert Scope.of(SCOPE).key in service._tasks, "and its write was scheduled"  # noqa: SLF001
    await service.settle()
    after = await service.read(list(SCOPE))
    assert after.rollup is not None and after.rollup["through_day"] > built.rollup["through_day"]
    await service.settle()
