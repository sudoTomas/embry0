"""Reading each forge account's listing concurrently, within one deadline (#340 review).

No test here sleeps for long: a slow account waits on an event that is never
set, and the deadline is a fraction of a second.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from embry0.api.forge_listings import PastDeadline, read_each


async def _hang(_entry: str) -> str:
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


async def test_each_result_comes_back_in_the_order_of_the_entries() -> None:
    async def read(entry: str) -> str:
        await asyncio.sleep(0.01 if entry == "a" else 0)
        if entry == "b":
            raise ValueError("refused")
        return entry.upper()

    results = await read_each(["a", "b", "c"], read, concurrency=3, deadline=5)
    assert results[0] == "A" and isinstance(results[1], ValueError) and results[2] == "C", (
        f"each entry's own result, or the exception it raised, in the entries' order: {results}"
    )


async def test_no_more_than_the_concurrency_run_at_once() -> None:
    running = peak = 0

    async def read(entry: int) -> int:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return entry

    assert await read_each(list(range(10)), read, concurrency=3, deadline=5) == list(range(10))
    assert peak == 3, f"at most 3 accounts are read at once, and 3 are: {peak}"


async def test_a_read_not_done_at_the_deadline_is_cancelled_and_reported() -> None:
    cancelled: list[str] = []

    async def read(entry: str) -> str:
        if entry.startswith("slow"):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(entry)
                raise
        return entry

    started = time.monotonic()
    results = await read_each(["slow-1", "fast", "slow-2"], read, concurrency=4, deadline=0.1)
    took = time.monotonic() - started
    assert took < 2, f"the deadline holds however long a read would take: {took:.2f}s"
    assert isinstance(results[0], PastDeadline) and results[1] == "fast" and isinstance(results[2], PastDeadline), (
        f"a read past the deadline is reported as such, the others kept: {results}"
    )
    assert sorted(cancelled) == ["slow-1", "slow-2"], f"the reads past the deadline are cancelled: {cancelled}"


async def test_reads_waiting_for_a_slot_count_against_the_same_deadline() -> None:
    """With one slot, a hanging first account leaves the others queued; all of them end at the deadline."""
    started = time.monotonic()
    results = await read_each(["a", "b", "c"], _hang, concurrency=1, deadline=0.1)
    assert time.monotonic() - started < 2
    assert all(isinstance(r, PastDeadline) for r in results), results


async def test_a_cancelled_caller_cancels_every_read() -> None:
    cancelled: list[str] = []

    async def read(entry: str) -> str:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(entry)
            raise
        return entry

    caller = asyncio.create_task(read_each(["a", "b"], read, concurrency=2, deadline=60))
    await asyncio.sleep(0.02)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert sorted(cancelled) == ["a", "b"], f"no read outlives the request that started it: {cancelled}"
