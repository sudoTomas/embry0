"""The global cap on jobs running at once (#583).

Every sandbox shares one DinD daemon, so the number of jobs running together
is what decides whether they fit in its memory. A job holds one slot while
its runner runs and gives it back when the runner returns, however it ends.
A job that pauses for an answer has returned, so it holds none; its resume
claims a slot again, with priority over jobs that have not started yet.

In-process and in-memory, like the background tasks it gates: a restart
loses the queue. A job that was waiting in it is still ``pending`` with its
launch record, and boot recovery starts it again (``relaunch`` in
``embry0.orchestration.restart``); one with no launch record is failed
(``JobsRepository.fail_orphaned``).
"""

from __future__ import annotations

import asyncio
from collections import deque


class RunSlot:
    """One job's claim on a run slot. ``release()`` is idempotent and must
    always run, whether or not the slot was ever granted."""

    __slots__ = ("_gate", "_granted", "_released", "_waiter", "job_id")

    def __init__(self, gate: RunSlots, job_id: str) -> None:
        self.job_id = job_id
        self._gate = gate
        self._granted = False
        self._released = False
        self._waiter: asyncio.Future[None] | None = None

    @property
    def granted(self) -> bool:
        return self._granted

    async def wait(self) -> None:
        """Return once the slot is granted. Cancelling the wait does not
        release the claim: the caller's ``finally`` does that."""
        if self._granted:
            return
        if self._waiter is None:
            self._waiter = asyncio.get_running_loop().create_future()
        await self._waiter

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._gate._release(self)


class RunSlots:
    """At most ``limit`` granted slots; ``0`` means no limit.

    Claims past the limit wait in order. Priority claims (resumes) are served
    before ordinary ones, each kind first come, first served.
    """

    def __init__(self, limit: int) -> None:
        if limit < 0:
            raise ValueError(f"run slot limit must be 0 (no cap) or more, got {limit}")
        self._limit = limit
        self._holders: set[RunSlot] = set()
        self._priority: deque[RunSlot] = deque()
        self._ordinary: deque[RunSlot] = deque()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def running(self) -> int:
        return len(self._holders)

    @property
    def waiting(self) -> int:
        return len(self._priority) + len(self._ordinary)

    def claim(self, job_id: str, *, priority: bool = False) -> RunSlot:
        """Take a slot now if one is free, else join the queue."""
        slot = RunSlot(self, job_id)
        if self._limit == 0 or len(self._holders) < self._limit:
            self._grant(slot)
        else:
            (self._priority if priority else self._ordinary).append(slot)
        return slot

    def position(self, slot: RunSlot) -> int | None:
        """1-based place in the queue, or None when not waiting."""
        for n, queued in enumerate((*self._priority, *self._ordinary), start=1):
            if queued is slot:
                return n
        return None

    def _grant(self, slot: RunSlot) -> None:
        self._holders.add(slot)
        slot._granted = True
        # A waiter whose task was cancelled has a cancelled future: the slot
        # is still granted, and that task's ``finally`` releases it.
        if slot._waiter is not None and not slot._waiter.done():
            slot._waiter.set_result(None)

    def _release(self, slot: RunSlot) -> None:
        if slot in self._holders:
            self._holders.discard(slot)
            self._grant_next()
            return
        for queue in (self._priority, self._ordinary):
            if slot in queue:
                queue.remove(slot)
                return

    def _grant_next(self) -> None:
        while self._limit == 0 or len(self._holders) < self._limit:
            if self._priority:
                self._grant(self._priority.popleft())
            elif self._ordinary:
                self._grant(self._ordinary.popleft())
            else:
                return
