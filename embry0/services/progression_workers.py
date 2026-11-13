"""The progression's computations in worker processes, off the API's event loop.

A read of the progression evaluates the achievements, quests and diaries
over every row of its scope, and folds every event of that history when the
scope's rollup is missing or unusable; a write of a rollup folds the history
once more. On a large history that is seconds of Python, and on the API's
event loop every other request waits as long. A thread would not help: the
GIL lets only one thread run Python at a time, so the loop would still wait
on it, and several folds at once would share one core.

So a service with :class:`FoldWorkers` sends each computation to a worker
process. Each process has an event loop and a database pool of its own, over
the same database, and runs the same
:class:`~embry0.services.progression.Computation` a service without workers
runs on its loop: the same queries, the same fold, the same answer. Only the
rules, the scope and the clock go to a worker, and only the progression, or
a rollup's new head, comes back: tens of kilobytes.

- **Two lanes.** The deployment's computations have a process of their own;
  the keys' sets of repositories share the others. A crash, a hang or a
  cancelled caller in a key's fold never takes the deployment's process.
- **One computation per process,** each process its own executor, so a
  process that dies fails only the computation it was running. That one is
  run once more on a new process; one that kills its process twice fails,
  alone, and is logged with its scope.
- **A time limit.** A computation not done after its limit (:data:`READ_LIMIT`,
  :data:`WRITE_LIMIT`) has its process killed and replaced, and fails with
  :class:`FoldTimeout`; its caller's locks are released with it. A read's
  limit counts its wait for a free process too. The service schedules the
  scope's rollup build as such a read fails, and before it sends a read of a
  scope with no rollup, so the build goes on in the background and the reads
  after it answer from the rollup.
- **Sized to the CPUs the orchestrator may use** (:func:`fold_processes`): the
  cgroup's CPU quota, not the host's count, less one for the API itself.
- A process exits when the orchestrator that started it is gone.
"""

from __future__ import annotations

import asyncio
import math
import multiprocessing
import os
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from datetime import datetime
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

import structlog

if TYPE_CHECKING:
    from embry0.services.progression import Computation, Progression
    from embry0.services.progression_pack import Rules
    from embry0.services.progression_rollup import Maintained, Scope
    from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

#: The worker processes at most, the deployment's included (:func:`fold_processes` takes fewer on fewer CPUs).
FOLD_WORKERS = 4
#: Seconds a read may wait for a process and compute before it fails, its process killed: under the 30 s a dashboard
#: request waits, so the caller sees the 503 and its Retry-After. The rollup build its scope needs goes on meanwhile.
READ_LIMIT = 25.0
#: Seconds a rollup write may take: a full build folds the whole history, in the background.
WRITE_LIMIT = 600.0

T = TypeVar("T")


class FoldTimeout(Exception):
    """A computation of the progression ran past its time limit; its process was killed and replaced."""


def available_cpus(cgroup: Path = Path("/sys/fs/cgroup"), proc: Path = Path("/proc/self/cgroup")) -> float:
    """The CPUs this process may use: the ones it may run on, at most its cgroup's CPU quota.

    A container's ``--cpus`` is a quota (cgroup v2 ``cpu.max``, v1
    ``cpu.cfs_quota_us`` over ``cpu.cfs_period_us``) the host's CPU count does
    not show. The quota of the process's own cgroup and every one above it
    counts; the smallest wins.
    """
    try:
        cpus = float(len(os.sched_getaffinity(0)))
    except AttributeError:
        cpus = float(os.cpu_count() or 1)
    quotas = [q for q in (_quota_v2(cgroup, proc), _quota_v1(cgroup)) if q is not None]
    return min([cpus, *quotas])


def _quota_v2(cgroup: Path, proc: Path) -> float | None:
    try:
        own = next(line[3:].strip() for line in proc.read_text().splitlines() if line.startswith("0::"))
    except (OSError, StopIteration):
        own = "/"
    found: list[float] = []
    place = cgroup / own.lstrip("/")
    while True:
        try:
            quota, period = (place / "cpu.max").read_text().split()[:2]
            if quota != "max":
                found.append(int(quota) / int(period))
        except (OSError, ValueError):
            pass
        if place == cgroup or cgroup not in place.parents:
            break
        place = place.parent
    return min(found) if found else None


def _quota_v1(cgroup: Path) -> float | None:
    # The controller is mounted as cpu,cpuacct; most hosts link cpu to it, some do not.
    for controller in ("cpu", "cpu,cpuacct"):
        try:
            quota = int((cgroup / controller / "cpu.cfs_quota_us").read_text())
            period = int((cgroup / controller / "cpu.cfs_period_us").read_text())
        except (OSError, ValueError):
            continue
        return quota / period if quota > 0 and period > 0 else None
    return None


def fold_processes(configured: int = FOLD_WORKERS, cpus: float | None = None) -> int:
    """How many worker processes: ``configured`` at most, one CPU left to the API, and two at least (the
    deployment's and one for the keys' sets)."""
    usable = available_cpus() if cpus is None else cpus
    return max(2, min(configured, math.floor(usable) - 1))


class _Slot:
    """One worker process, in an executor of its own, made at its first computation and again after it is lost."""

    def __init__(self, initargs: tuple[Any, ...]) -> None:
        self._initargs = initargs
        self.executor: ProcessPoolExecutor | None = None

    def ready(self) -> ProcessPoolExecutor:
        if self.executor is None:
            self.executor = ProcessPoolExecutor(
                max_workers=1,
                # Spawned, not forked: a fork would copy the API's loop, its threads and its pool's sockets.
                mp_context=multiprocessing.get_context("spawn"),
                initializer=_start,
                initargs=self._initargs,
            )
        return self.executor

    def processes(self) -> list[BaseProcess]:
        # The executor's own processes, by pid: it can wait for them, not stop them.
        return [] if self.executor is None else list(self.executor._processes.values())  # noqa: SLF001

    def discard(self, executor: ProcessPoolExecutor) -> None:
        """Kill ``executor``'s process, if it is still this slot's: the next computation starts a new one."""
        if self.executor is not executor:
            return
        processes = self.processes()
        self.executor = None
        executor.shutdown(wait=False, cancel_futures=True)
        for process in processes:
            process.kill()


class FoldWorkers:
    """Runs a progression service's computations in worker processes over ``database_url``: one for the
    deployment's, ``processes - 1`` for the keys' sets of repositories."""

    def __init__(
        self,
        database_url: str,
        *,
        processes: int = FOLD_WORKERS,
        json_logs: bool = True,
        read_limit: float = READ_LIMIT,
        write_limit: float = WRITE_LIMIT,
    ) -> None:
        if processes < 2:
            raise ValueError("fold workers need two processes at least: the deployment's and one for the sets")
        initargs = (database_url, json_logs)
        self._url = database_url
        self._processes = processes
        self._limits = {"read": read_limit, "write": write_limit}
        self._slots = [_Slot(initargs) for _ in range(processes)]
        self._lanes: dict[bool, asyncio.Queue[_Slot]] = {False: asyncio.Queue(), True: asyncio.Queue()}
        self._lanes[False].put_nowait(self._slots[0])
        for slot in self._slots[1:]:
            self._lanes[True].put_nowait(slot)

    async def progression(
        self, rules: Rules, scope: Scope, *, through_rollup: bool, rollup: bool, now: datetime
    ) -> tuple[Progression, bool]:
        """``Computation.progression`` in a worker; ``rollup`` False reads no rollup, ``now`` is the rollup's clock."""
        return await self._run(scope, "read", _progression, rules, scope, through_rollup, rollup, now)

    async def maintain(self, rules: Rules, scope: Scope, *, force_since: datetime | None, now: datetime) -> Maintained:
        """``Computation.maintain`` in a worker, with ``now`` as the rollup's clock."""
        return await self._run(scope, "write", _maintain, rules, scope, force_since, now)

    async def _run(self, scope: Scope, kind: str, work: Callable[..., T], *args: Any) -> T:
        lane = self._lanes[scope.repos is not None]
        limit = self._limits[kind]
        loop = asyncio.get_running_loop()
        # A read's limit counts its wait for a process too: a caller waits on both. A write runs in the background
        # and waits its turn as long as it takes, its limit counted from its start.
        deadline = loop.time() + limit if kind == "read" else None
        crashes = 0
        while True:
            try:
                slot = await asyncio.wait_for(lane.get(), None if deadline is None else deadline - loop.time())
            except TimeoutError:
                logger.warning("progression_fold_waited_too_long", scope=scope.key, kind=kind, limit_seconds=limit)
                raise FoldTimeout(f"the progression's {kind} of {scope.key or 'the deployment'} waited past {limit}s")
            executor = slot.ready()
            try:
                future: Future[T] = executor.submit(work, *args)
            except BrokenProcessPool:
                # Its process died between two computations: not this one's doing.
                slot.discard(executor)
                lane.put_nowait(slot)
                continue
            expired = threading.Event()

            def expire(
                slot: _Slot = slot,
                executor: ProcessPoolExecutor = executor,
                future: Future[T] = future,
                expired: threading.Event = expired,
            ) -> None:
                if not future.done():
                    expired.set()
                    slot.discard(executor)

            timer = loop.call_later(limit if deadline is None else max(deadline - loop.time(), 0.0), expire)

            def release(
                done: Future[T],
                slot: _Slot = slot,
                executor: ProcessPoolExecutor = executor,
                timer: asyncio.TimerHandle = timer,
            ) -> None:
                """On the loop, once the computation is over (not when a caller stops waiting for it): the slot goes
                back to its lane, with a new process to come if this one was lost."""
                timer.cancel()
                if not done.cancelled() and isinstance(done.exception(), BrokenProcessPool):
                    slot.discard(executor)
                lane.put_nowait(slot)

            def done(finished: Future[T], release: Callable[[Future[T]], None] = release) -> None:
                loop.call_soon_threadsafe(release, finished)

            future.add_done_callback(done)
            try:
                return await asyncio.wrap_future(future)
            except BrokenProcessPool:
                if expired.is_set():
                    logger.warning("progression_fold_timed_out", scope=scope.key, kind=kind, limit_seconds=limit)
                    raise FoldTimeout(f"the progression's {kind} of {scope.key or 'the deployment'} ran past {limit}s")
                crashes += 1
                if crashes == 2:
                    logger.error("progression_fold_crashed_twice", scope=scope.key, kind=kind)
                    raise
                logger.warning("progression_fold_worker_lost", scope=scope.key, kind=kind)

    async def aclose(self, *, timeout: float) -> None:
        """Stop the processes, a computation in one included: an open transaction rolls back with its connection."""
        processes: list[BaseProcess] = []
        for slot in self._slots:
            executor, processes_of = slot.executor, slot.processes()
            slot.executor = None
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
            processes += processes_of
        for process in processes:
            process.terminate()
        await asyncio.to_thread(_join, processes, timeout)


def _join(processes: Sequence[BaseProcess], timeout: float) -> None:
    for process in processes:
        process.join(timeout)
        if process.is_alive():
            process.kill()
            process.join(1)


# ---- in a worker process ----

_worker: _Worker | None = None

#: How often a worker checks that the orchestrator that started it is still there.
PARENT_CHECK = 1.0


class _Worker:
    """A worker process's event loop and database pool, made at its start and kept for its life."""

    def __init__(self, database_url: str) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._url = database_url
        self._db: DatabasePool | None = None

    async def computation(self, *, rollup: bool, now: datetime) -> Computation:
        from embry0.services.progression import Computation
        from embry0.services.progression_rollup import ProgressionRollup
        from embry0.storage.database import DatabasePool
        from embry0.storage.repositories.progression import ProgressionRepository
        from embry0.storage.repositories.progression_rollup import ProgressionRollupRepository

        if self._db is None:
            # Two connections: a rollup write holds one in its transaction while it reads the rows on the other.
            db = DatabasePool(self._url, min_size=1, max_size=2)
            await db.connect()
            self._db = db
        repository = ProgressionRepository(self._db)
        store = ProgressionRollupRepository(self._db)
        return Computation(repository, ProgressionRollup(store, repository, now=lambda: now) if rollup else None)


def _watch_parent(parent: int) -> None:
    """Exit once the process that started this one is gone (its children are given to another): killed with
    SIGKILL, it never stopped this process, and outside a container nothing else would."""
    while os.getppid() == parent:
        time.sleep(PARENT_CHECK)
    os._exit(1)


def _start(database_url: str, json_logs: bool) -> None:
    from embry0.observability import configure_structlog

    global _worker
    threading.Thread(target=_watch_parent, args=(os.getppid(),), name="parent-watch", daemon=True).start()
    configure_structlog(json=json_logs)
    _worker = _Worker(database_url)


def _the_worker() -> _Worker:
    if _worker is None:
        raise RuntimeError("not a fold worker process")
    return _worker


def _progression(
    rules: Rules, scope: Scope, through_rollup: bool, rollup: bool, now: datetime
) -> tuple[Progression, bool]:
    worker = _the_worker()

    async def run() -> tuple[Progression, bool]:
        computation = await worker.computation(rollup=rollup, now=now)
        return await computation.progression(rules, scope, through_rollup=through_rollup)

    return worker.loop.run_until_complete(run())


def _maintain(rules: Rules, scope: Scope, force_since: datetime | None, now: datetime) -> Maintained:
    worker = _the_worker()

    async def run() -> Maintained:
        computation = await worker.computation(rollup=True, now=now)
        return await computation.maintain(rules, scope, force_since=force_since)

    return worker.loop.run_until_complete(run())
