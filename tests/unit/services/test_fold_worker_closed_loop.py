"""A fold worker's computation that ends after the event loop that started it closed
(``embry0/services/progression_workers.py``), with no database."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

import pytest

from embry0.services.progression_rollup import Scope
from embry0.services.progression_workers import FoldWorkers

ONE = Scope.of(["acme/one"])

#: Seconds the test waits for its computation to start: a bound for a hang, not a measure of speed.
WITHIN = 60.0


def hold(held: str, gate: str) -> int:
    """In a worker: write its pid to ``held``, then keep its process until ``gate`` exists; answer the pid."""
    Path(f"{held}.part").write_text(str(os.getpid()))
    os.replace(f"{held}.part", held)
    while not os.path.exists(gate):
        time.sleep(0.01)
    return os.getpid()


def test_a_computation_that_ends_after_its_loop_closed_ends_quietly(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A caller's loop can close while its computation still runs (a shutdown, a caller's loop that ended): the
    computation's end, in the executor's thread, has no loop to give its process back to, and raises nothing."""
    held, gate = tmp_path / "held", tmp_path / "gate"
    workers = FoldWorkers("postgresql://unused/unused", processes=3, json_logs=False)

    async def leave_it_running() -> None:
        # The caller is cancelled, and the loop closed, as asyncio.run ends; the computation runs on.
        asyncio.get_running_loop().create_task(workers._run(ONE, "read", hold, str(held), str(gate)))  # noqa: SLF001
        while not held.exists():
            await asyncio.sleep(0.01)

    executors = []
    try:
        with caplog.at_level(logging.ERROR, logger="concurrent.futures"):
            asyncio.run(asyncio.wait_for(leave_it_running(), WITHIN))
            executors = [slot.executor for slot in workers._slots if slot.executor is not None]  # noqa: SLF001
            assert len(executors) == 1, "the computation's process"
            gate.touch()  # it ends now, its loop closed
            executors[0].shutdown(wait=True)  # its end, and the executor's callbacks on it, are over
    finally:
        gate.touch()
        for executor in executors:
            executor.shutdown(wait=True, cancel_futures=True)
    raised = [record.getMessage() for record in caplog.records if record.name == "concurrent.futures"]
    assert raised == [], f"its end raised in the executor's thread: {raised}"
