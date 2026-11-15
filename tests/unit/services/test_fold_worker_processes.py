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
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import subprocess
import sys
import textwrap
import time
from collections.abc import AsyncIterator
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from embry0.services.progression_rollup import EVERY, Scope
from embry0.services.progression_workers import READ_LIMIT, FoldTimeout, FoldWorkers, available_cpus, fold_processes

pytestmark = pytest.mark.asyncio

ONE, TWO = Scope.of(["acme/one"]), Scope.of(["acme/two"])


@pytest.fixture
async def workers() -> AsyncIterator[FoldWorkers]:
    found = FoldWorkers("postgresql://unused/unused", processes=3, json_logs=False, read_limit=2.0)
    try:
        yield found
    finally:
        await found.aclose(timeout=5)


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


def _gone(pid: int, within: float = 5.0) -> bool:
    """Whether ``pid`` is gone by ``within`` seconds from now: a killed process takes a moment to go."""
    deadline = time.monotonic() + within
    while _alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


async def test_the_sets_never_take_the_deployment_s_process(workers: FoldWorkers) -> None:
    """Both processes of the sets busy, one of them for a caller that left: a set waits, the deployment runs."""
    await asyncio.gather(*(workers._run(scope, "read", time.sleep, 0.2) for scope in (ONE, TWO, EVERY)))  # noqa: SLF001
    left = asyncio.create_task(workers._run(ONE, "read", time.sleep, 1.5))  # noqa: SLF001
    busy = asyncio.create_task(workers._run(TWO, "read", time.sleep, 1.5))  # noqa: SLF001
    await asyncio.sleep(0.5)
    left.cancel()
    started = time.monotonic()
    waiting = asyncio.create_task(workers._run(ONE, "read", os.getpid))  # noqa: SLF001
    deployment = await asyncio.wait_for(workers._run(EVERY, "read", os.getpid), 10)  # noqa: SLF001
    assert time.monotonic() - started < 1.0, "the deployment's computation waited for the sets'"
    await waiting
    assert time.monotonic() - started > 0.8, "a set's computation took a process whose caller had left mid-fold"
    await busy
    assert deployment not in {p.pid for slot in workers._slots[1:] for p in slot.processes()}  # noqa: SLF001


async def test_a_computation_that_kills_its_process_runs_again_once_and_fails_alone(
    workers: FoldWorkers, tmp_path: Path
) -> None:
    flag = tmp_path / "flag"
    again = await workers._run(ONE, "read", crash_once, str(flag))  # noqa: SLF001
    killed = int(flag.read_text())
    assert again != killed and _alive(again) and _gone(killed), "it ran again on a live process"

    others = [
        asyncio.create_task(workers._run(scope, "read", time.sleep, 1.0))  # noqa: SLF001
        for scope in (TWO, EVERY)
    ]
    await asyncio.sleep(0.2)
    with capture_logs() as logs, pytest.raises(BrokenProcessPool):
        await workers._run(ONE, "read", os._exit, 1)  # noqa: SLF001
    assert [(e["event"], e["scope"]) for e in logs if e["event"].startswith("progression_fold")] == [
        ("progression_fold_worker_lost", ONE.key),
        ("progression_fold_crashed_twice", ONE.key),
    ], "run once more, then failed, each logged with its scope"
    assert await asyncio.gather(*others) == [None, None], "the computations in flight beside it finished"
    assert isinstance(await workers._run(ONE, "read", os.getpid), int), "and its lane serves the next"  # noqa: SLF001


async def test_a_computation_past_its_limit_loses_its_process() -> None:
    """One process for the sets, so the next computation shows whether it was replaced."""
    workers = FoldWorkers("postgresql://unused/unused", processes=2, json_logs=False, read_limit=2.0)
    try:
        await _past_its_limit(workers)
    finally:
        await workers.aclose(timeout=5)


async def _past_its_limit(workers: FoldWorkers) -> None:
    pid = await workers._run(ONE, "read", os.getpid)  # noqa: SLF001
    started = time.monotonic()
    with pytest.raises(FoldTimeout):
        await asyncio.wait_for(workers._run(ONE, "read", time.sleep, 30), 20)  # noqa: SLF001
    assert time.monotonic() - started < 6, "the limit is two seconds"
    after = await workers._run(ONE, "read", os.getpid)  # noqa: SLF001
    assert after != pid and _gone(pid), "the process past its limit was killed and replaced"

    # A stopped process answers nothing: the same, and its lane is free again after it.
    os.kill(after, signal.SIGSTOP)
    with pytest.raises(FoldTimeout):
        await asyncio.wait_for(workers._run(TWO, "read", os.getpid), 20)  # noqa: SLF001
    assert _gone(after), "the stopped process was killed"
    assert await workers._run(ONE, "read", os.getpid) not in {pid, after}, "and replaced"  # noqa: SLF001


async def test_a_worker_exits_when_the_process_that_started_it_is_killed(tmp_path: Path) -> None:
    script = textwrap.dedent(
        """
        import asyncio, os, sys, time
        from embry0.services.progression_rollup import EVERY
        from embry0.services.progression_workers import FoldWorkers

        async def main():
            workers = FoldWorkers("postgresql://unused/unused", processes=2, json_logs=False)
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
        worker = int(parent.stdout.readline())
        assert _alive(worker)
        parent.kill()
        parent.wait(10)
        deadline = time.monotonic() + 10
        while _alive(worker) and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
        assert not _alive(worker), "the worker outlived the process that started it"
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(10)
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
    assert [fold_processes(4, cpus) for cpus in (48.0, 4.0, 3.5, 2.0, 1.0)] == [4, 3, 2, 2, 2]


async def test_a_read_that_waits_past_its_limit_for_a_process_fails() -> None:
    """A read's limit counts its wait: with the sets' one process busy with a long write, a set's read fails at its
    limit instead of waiting for it."""
    workers = FoldWorkers("postgresql://unused/unused", processes=2, json_logs=False, read_limit=1.0)
    try:
        write = asyncio.create_task(workers._run(ONE, "write", time.sleep, 4))  # noqa: SLF001
        await asyncio.sleep(0.5)
        started = time.monotonic()
        with pytest.raises(FoldTimeout):
            await asyncio.wait_for(workers._run(TWO, "read", os.getpid), 10)  # noqa: SLF001
        assert time.monotonic() - started < 3, "it failed at its limit, not when the write was done"
        await write
    finally:
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
    workers = FoldWorkers("postgresql://unused/unused", processes=2, json_logs=False, write_limit=1.0)
    try:
        started = time.monotonic()
        with capture_logs() as logs, pytest.raises(FoldTimeout):
            await asyncio.wait_for(workers._run(ONE, "write", time.sleep, 30), 20)  # noqa: SLF001
        assert time.monotonic() - started < 5, "it failed at its limit, once"
        assert [e["event"] for e in logs if e["event"].startswith("progression_fold")] == ["progression_fold_timed_out"]
    finally:
        await workers.aclose(timeout=5)
