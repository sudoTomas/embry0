"""Async watchdog primitives for the QA pipeline.

- TotalBudgetWatchdog: fires once after budget_seconds.
- IdleWatchdog: fires when no heartbeat() call in grace_seconds.
- BootWatchdog: TotalBudgetWatchdog tied to phase=boot.

Each watchdog runs on its own asyncio task. on_timeout is awaited.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


class TotalBudgetWatchdog:
    def __init__(self, *, budget_seconds: float, on_timeout: Callable[[], Awaitable[None]]) -> None:
        self._budget = budget_seconds
        self._on_timeout = on_timeout
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        async def _wait_and_fire() -> None:
            await asyncio.sleep(self._budget)
            await self._on_timeout()

        self._task = asyncio.create_task(_wait_and_fire(), name="qa.total_budget")

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None


class IdleWatchdog:
    """Fires when no heartbeat() call in grace_seconds."""

    def __init__(self, *, grace_seconds: float, on_timeout: Callable[[], Awaitable[None]]) -> None:
        self._grace = grace_seconds
        self._on_timeout = on_timeout
        self._last = time.monotonic()
        self._task: asyncio.Task | None = None
        self._stopped = False

    def heartbeat(self) -> None:
        self._last = time.monotonic()

    def start(self) -> None:
        async def _loop() -> None:
            poll = min(self._grace / 4.0, 1.0)
            while not self._stopped:
                await asyncio.sleep(poll)
                if (time.monotonic() - self._last) >= self._grace:
                    await self._on_timeout()
                    return

        self._stopped = False
        self._task = asyncio.create_task(_loop(), name="qa.idle")

    def stop(self) -> None:
        self._stopped = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None


class BootWatchdog:
    """Wrapper around TotalBudgetWatchdog tied to the boot phase."""

    def __init__(self, *, boot_budget_seconds: float, on_timeout: Callable[[], Awaitable[None]]) -> None:
        self._inner = TotalBudgetWatchdog(budget_seconds=boot_budget_seconds, on_timeout=on_timeout)

    def start(self) -> None:
        self._inner.start()

    def stop(self) -> None:
        self._inner.stop()
