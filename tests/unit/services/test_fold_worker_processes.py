"""How the fold workers treat their processes (``embry0/services/progression_workers.py``), with no database.

Each test runs plain functions in real worker processes:

- the deployment's lane has a process of its own, which the sets of
  repositories never take, however many of them run or how their callers
  leave;
- a computation that kills its process runs once more on a new one, and fails
  alone when it kills that one too; the others in flight finish;
- a computation past its time limit has its process killed and replaced, and
  fails with ``FoldTimeout``, a stopped process included;
- a worker exits when the process that started it is killed;
- the pool is sized to the cgroup's CPU quota, not the host's count.

A loaded host can take seconds to start a worker process, so no test measures
how long anything took. A computation that must still be running is a
:class:`_Hold`, which keeps its process until the test lets it go; a time
limit is short only for the computation meant to run past it; and every wait
for what must happen is bounded by :data:`WITHIN`, a bound for a hang, not a
measure of speed.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
import subprocess
import sys
import textwrap
import time
from collections.abc import AsyncIterator, Awaitable, Coroutine, Iterator
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from embry0.services.progression_rollup import EVERY, Scope
from embry0.services.progression_workers import READ_LIMIT, FoldTimeout, FoldWorkers, available_cpus, fold_processes

pytestmark = pytest.mark.asyncio

ONE, TWO = Scope.of(["acme/one"]), Scope.of(["acme/two"])

#: Seconds a test waits for what must happen before it fails: a bound for a hang, never a measure of speed.
WITHIN = 60.0
#: The workers' read limit here: past :data:`WITHIN`, so a computation that never answers fails on the test's bound,
#: with the test's message, and no computation runs into it on a loaded host.
LONG = 2 * WITHIN
#: A time limit meant to run out: only the computation that should run past it gets it (:func:`_limit`).
SHORT = 1.0


@pytest.fixture
async def workers() -> AsyncIterator[FoldWorkers]:
    found = FoldWorkers("postgresql://unused/unused", processes=4, json_logs=False, read_limit=LONG)
    try:
        yield found
    finally:
        await found.aclose(timeout=5)


async def _soon[T](work: Awaitable[T], failure: str) -> T:
    """``work``'s answer, or a failure saying ``failure`` when it has none within :data:`WITHIN`. Its own
    :class:`FoldTimeout` goes through to the caller."""
    try:
        return await asyncio.wait_for(work, WITHIN)
    except TimeoutError as exc:
        raise AssertionError(failure) from exc


@contextlib.contextmanager
def _limit(workers: FoldWorkers, kind: str) -> Iterator[None]:
    """``kind``'s time limit at :data:`SHORT` meanwhile, for the computation meant to run past it; the others keep
    their own, which leaves them all the time a loaded host takes to start a process."""
    before = workers._limits[kind]  # noqa: SLF001
    workers._limits[kind] = SHORT  # noqa: SLF001
    try:
        yield
    finally:
        workers._limits[kind] = before  # noqa: SLF001


def hold(held: str, gate: str) -> int:
    """In a worker: write its pid to ``held``, then keep its process until ``gate`` exists; answer the pid."""
    Path(f"{held}.part").write_text(str(os.getpid()))
    os.replace(f"{held}.part", held)
    while not os.path.exists(gate):
        time.sleep(0.01)
    return os.getpid()


class _Hold:
    """A computation that keeps its worker process until the test lets it go: running, whatever the clock says."""

    def __init__(self, tmp_path: Path, name: str) -> None:
        self._held, self._gate = tmp_path / f"{name}.held", tmp_path / f"{name}.gate"

    def run(self, workers: FoldWorkers, scope: Scope, kind: str = "read") -> Coroutine[Any, Any, int]:
        return workers._run(scope, kind, hold, str(self._held), str(self._gate))  # noqa: SLF001

    async def pid(self) -> int:
        """The pid of the process it holds, once it holds one."""

        async def held() -> int:
            while not self._held.exists():
                await asyncio.sleep(0.01)
            return int(self._held.read_text())

        return await _soon(held(), f"no process took {self._held.stem}'s computation")

    def release(self) -> None:
        self._gate.touch()


def crash_once(flag: str) -> int:
    """In a worker: kill the process the first time, writing its pid to ``flag``; answer the pid the next."""
    if not os.path.exists(flag):
        Path(flag).write_text(str(os.getpid()))
        os._exit(1)
    return os.getpid()


def _alive(pid: int) -> bool:
    """Whether ``pid`` runs (or is stopped): not gone, a zombie or dead."""
    try:
        status = Path(f"/proc/{pid}/status").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return False
    state = next((line.split()[1] for line in status.splitlines() if line.startswith("State:")), "X")
    return state not in {"Z", "X"}


def _gone(pid: int) -> bool:
    """Whether ``pid`` is gone within :data:`WITHIN` seconds: a killed process takes a moment to go."""
    deadline = time.monotonic() + WITHIN
    while _alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


async def test_the_sets_never_take_the_deployment_s_process(workers: FoldWorkers, tmp_path: Path) -> None:
    """Both processes of the sets held, one of them for a caller that left: a set waits, the deployment runs."""
    left_hold, busy_hold = _Hold(tmp_path, "left"), _Hold(tmp_path, "busy")
    left = asyncio.create_task(left_hold.run(workers, ONE))
    busy = asyncio.create_task(busy_hold.run(workers, TWO))
    try:
        held = {await left_hold.pid(), await busy_hold.pid()}
        left.cancel()
        waiting = asyncio.create_task(workers._run(ONE, "read", os.getpid))  # noqa: SLF001
        deployment = await _soon(
            workers._run(EVERY, "read", os.getpid),  # noqa: SLF001
            "the deployment's computation waited for the sets'",
        )
        assert deployment not in held, "the deployment ran in a process of the sets'"
        assert not waiting.done(), "a set's computation ran while both the sets' processes were held"
        busy_hold.release()
        freed = await _soon(busy, "the set's computation never finished")
        assert (
            await _soon(waiting, "a set's computation waited for a process whose caller had left mid-fold") == freed
        ), "it took the process freed, not the one still folding for a caller that had left"
    finally:
        left_hold.release()
        busy_hold.release()
    assert deployment not in {p.pid for slot in workers._slots[1:] for p in slot.processes()}  # noqa: SLF001
    assert workers._slots[1].processes() == [], "nothing but a deployment's build takes the builds' process"  # noqa: SLF001


async def test_a_computation_that_kills_its_process_runs_again_once_and_fails_alone(
    workers: FoldWorkers, tmp_path: Path
) -> None:
    flag = tmp_path / "flag"
    again = await _soon(workers._run(ONE, "read", crash_once, str(flag)), "it never ran again")  # noqa: SLF001
    killed = int(flag.read_text())
    assert again != killed and _alive(again) and _gone(killed), "it ran again on a live process"

    holds = [_Hold(tmp_path, "two"), _Hold(tmp_path, "every")]
    others = [asyncio.create_task(each.run(workers, scope)) for each, scope in zip(holds, (TWO, EVERY), strict=True)]
    try:
        beside = [await each.pid() for each in holds]
        with capture_logs() as logs, pytest.raises(BrokenProcessPool):
            await _soon(workers._run(ONE, "read", os._exit, 1), "it never failed")  # noqa: SLF001
        assert [(e["event"], e["scope"]) for e in logs if e["event"].startswith("progression_fold")] == [
            ("progression_fold_worker_lost", ONE.key),
            ("progression_fold_crashed_twice", ONE.key),
        ], "run once more, then failed, each logged with its scope"
        assert all(_alive(pid) for pid in beside) and not any(other.done() for other in others), (
            "the computations in flight beside it went on"
        )
    finally:
        for each in holds:
            each.release()
    assert await _soon(asyncio.gather(*others), "they never finished") == beside, "and finished where they ran"
    assert isinstance(await _soon(workers._run(ONE, "read", os.getpid), "its lane is stuck"), int), (  # noqa: SLF001
        "and its lane serves the next"
    )


async def test_a_computation_past_its_limit_loses_its_process(tmp_path: Path) -> None:
    """One process for the sets, so the next computation shows whether it was replaced."""
    workers = FoldWorkers("postgresql://unused/unused", processes=3, json_logs=False, read_limit=LONG)
    try:
        await _past_its_limit(workers, _Hold(tmp_path, "never"))
    finally:
        await workers.aclose(timeout=5)


async def _past_its_limit(workers: FoldWorkers, never: _Hold) -> None:
    pid = await _soon(workers._run(ONE, "read", os.getpid), "the first computation never ran")  # noqa: SLF001
    # Its gate never opens: only the limit ends it.
    with _limit(workers, "read"), pytest.raises(FoldTimeout):
        await _soon(never.run(workers, ONE), "a computation ran past its limit")
    assert _gone(pid), "the process past its limit was killed"
    after = await _soon(workers._run(ONE, "read", os.getpid), "its lane is stuck")  # noqa: SLF001
    assert after != pid, "and replaced"

    # A stopped process answers nothing: the same, and its lane is free again after it.
    os.kill(after, signal.SIGSTOP)
    with _limit(workers, "read"), pytest.raises(FoldTimeout):
        await _soon(workers._run(TWO, "read", os.getpid), "a stopped process held its computation past its limit")  # noqa: SLF001
    assert _gone(after), "the stopped process was killed"
    assert await _soon(workers._run(ONE, "read", os.getpid), "its lane is stuck") not in {pid, after}, "and replaced"  # noqa: SLF001


async def test_a_worker_exits_when_the_process_that_started_it_is_killed(tmp_path: Path) -> None:
    script = textwrap.dedent(
        """
        import asyncio, os, sys, time
        from embry0.services.progression_rollup import EVERY
        from embry0.services.progression_workers import FoldWorkers

        async def main():
            workers = FoldWorkers("postgresql://unused/unused", processes=3, json_logs=False)
            print(await workers._run(EVERY, "read", os.getpid), flush=True)
            time.sleep(60)

        asyncio.run(main())
        """
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)},
    )
    try:
        assert parent.stdout is not None
        worker = int(await _soon(asyncio.to_thread(parent.stdout.readline), "the parent's computation never ran"))
        assert _alive(worker)
        parent.kill()
        parent.wait(WITHIN)
        assert _gone(worker), "the worker outlived the process that started it"
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(WITHIN)
        if parent.stdout is not None:
            parent.stdout.close()


def _cgroup(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


async def test_the_cpus_are_the_cgroup_s_quota_not_the_host_s(tmp_path: Path) -> None:
    host = float(len(os.sched_getaffinity(0)))
    proc = tmp_path / "proc-cgroup"
    proc.write_text("0::/\n")
    v2 = _cgroup(tmp_path / "v2", {"cpu.max": "400000 100000\n"})
    assert available_cpus(v2, proc) == min(host, 4.0), "a container's --cpus 4 on cgroup v2"
    nested = _cgroup(tmp_path / "nested", {"cpu.max": "max 100000\n", "app/cpu.max": "250000 100000\n"})
    (tmp_path / "nested-proc").write_text("0::/app\n")
    assert available_cpus(nested, tmp_path / "nested-proc") == min(host, 2.5), "the process's own cgroup"
    unlimited = _cgroup(tmp_path / "max", {"cpu.max": "max 100000\n"})
    assert available_cpus(unlimited, proc) == host, "no quota: the CPUs it may run on"
    v1 = _cgroup(tmp_path / "v1", {"cpu/cpu.cfs_quota_us": "300000\n", "cpu/cpu.cfs_period_us": "100000\n"})
    assert available_cpus(v1, tmp_path / "none") == min(host, 3.0), "cgroup v1"
    v1_joined = _cgroup(
        tmp_path / "v1-joined",
        {"cpu,cpuacct/cpu.cfs_quota_us": "200000\n", "cpu,cpuacct/cpu.cfs_period_us": "100000\n"},
    )
    assert available_cpus(v1_joined, tmp_path / "none") == min(host, 2.0), "cgroup v1 with no cpu link"
    assert [fold_processes(4, cpus) for cpus in (48.0, 4.0, 3.5, 2.0, 1.0)] == [4, 3, 3, 3, 3]


async def test_a_read_that_waits_past_its_limit_for_a_process_fails(tmp_path: Path) -> None:
    """A read's limit counts its wait: with the sets' one process held by a write, a set's read fails at its limit
    instead of waiting for it."""
    workers = FoldWorkers("postgresql://unused/unused", processes=3, json_logs=False, read_limit=LONG)
    writing = _Hold(tmp_path, "write")
    try:
        write = asyncio.create_task(writing.run(workers, ONE, "write"))
        await writing.pid()
        with _limit(workers, "read"), pytest.raises(FoldTimeout):
            await _soon(workers._run(TWO, "read", os.getpid), "the read waited for the write past its limit")  # noqa: SLF001
        assert not write.done(), "it failed at its limit, not when the write was done"
        writing.release()
        await _soon(write, "the write never finished")
    finally:
        writing.release()
        await workers.aclose(timeout=5)


async def test_a_read_fails_before_the_dashboard_stops_waiting_for_it() -> None:
    """The dashboard's requests give up after their timeout (``frontend/src/api/client.ts``): a read's limit, with
    room for the rest of the request, is under it, so the dashboard sees the 503 and its Retry-After."""
    client = Path(__file__).resolve().parents[3] / "frontend" / "src" / "api" / "client.ts"
    found = re.search(r"\btimeout:\s*([\d_]+)", client.read_text())
    assert found is not None, f"no timeout in {client}"
    dashboard = int(found.group(1).replace("_", "")) / 1000
    assert READ_LIMIT + 3 <= dashboard, f"a read's limit, {READ_LIMIT}s, is too close to the dashboard's {dashboard}s"


async def test_a_write_past_its_limit_fails_as_one_and_is_not_run_again() -> None:
    """A write that runs past its limit is no crash: it fails with ``FoldTimeout`` once, not again on a new process."""
    workers = FoldWorkers("postgresql://unused/unused", processes=3, json_logs=False, write_limit=SHORT)
    try:
        # A process that took longer than the limit to start is the same write past its limit.
        with capture_logs() as logs, pytest.raises(FoldTimeout):
            await _soon(workers._run(ONE, "write", time.sleep, 10 * WITHIN), "a write ran past its limit")  # noqa: SLF001
        assert [e["event"] for e in logs if e["event"].startswith("progression_fold")] == [
            "progression_fold_timed_out"
        ], "it failed at its limit, once"
    finally:
        await workers.aclose(timeout=5)


async def test_a_build_of_the_deployment_s_rollup_never_holds_up_its_reads(
    workers: FoldWorkers, tmp_path: Path
) -> None:
    """The deployment's builds have a process of their own: one held leaves its reads, and the sets', theirs."""
    building = _Hold(tmp_path, "build")
    build = asyncio.create_task(building.run(workers, EVERY, "write"))
    try:
        builder = await building.pid()
        read = await _soon(workers._run(EVERY, "read", os.getpid), "the deployment's read waited for the build")  # noqa: SLF001
        sets = await _soon(workers._run(ONE, "read", os.getpid), "a set's read waited for the build")  # noqa: SLF001
        assert not build.done() and builder not in {read, sets}, "they ran beside the build, not in its process"
        assert [p.pid for p in workers._slots[1].processes()] == [builder], "the build ran in the builds' process"  # noqa: SLF001
    finally:
        building.release()
    assert await _soon(build, "the build never finished") == builder
