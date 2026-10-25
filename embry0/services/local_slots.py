"""Slots on the operator's local model server (docs/providers.md, local models).

The server is shared hardware with a fixed capacity, so embry0 sends it at
most ``provider_config.local_max_runs`` agent runs at once (one by default).
Every run on the custom provider holds a slot, a lease in
``local_model_leases``, from just before the run until just after it:

- a run that only prefers the local model (local-first) takes a slot only if
  one is free now, and otherwise runs on its own cloud model;
- a run that has to use it (a fallback to it, a model a person named) waits
  for a slot up to ``LOCAL_LLM_SLOT_WAIT_SECONDS`` and then fails with
  ``ERR_LOCAL_MODEL_BUSY`` without having run.

A lease expires by itself after the run's own time limit, so one left by a
process that died frees its slot without anyone deleting it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)

POLL_SECONDS = 5.0


class _Leases(Protocol):
    async def try_take(self, *, cap: int, job_id: str, agent_type: str, model: str, run_seconds: int) -> str | None: ...

    async def release(self, lease_id: str) -> None: ...


class LocalSlots:
    """Take and give back the local model server's slots."""

    def __init__(
        self,
        leases: _Leases,
        *,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._leases = leases
        self._sleep = sleep
        self._clock = clock
        self._poll = poll_seconds

    async def try_acquire(self, *, cap: int, job_id: str, agent_type: str, model: str, run_seconds: int) -> str | None:
        """A lease id if a slot is free now, else None. A failed read counts as no slot."""
        try:
            return await self._leases.try_take(
                cap=cap, job_id=job_id, agent_type=agent_type, model=model, run_seconds=run_seconds
            )
        except Exception:  # noqa: BLE001 - no lease, no run on the shared server
            logger.warning("local_slot_take_failed", job_id=job_id, agent_type=agent_type, exc_info=True)
            return None

    async def acquire(
        self,
        *,
        cap: int,
        job_id: str,
        agent_type: str,
        model: str,
        run_seconds: int,
        wait_seconds: float,
        on_wait: Callable[[], None] | None = None,
    ) -> str | None:
        """A lease id once a slot is free, or None when none was within ``wait_seconds``.

        ``on_wait`` runs once, when the first try finds every slot taken.
        """

        async def attempt() -> str | None:
            return await self.try_acquire(
                cap=cap, job_id=job_id, agent_type=agent_type, model=model, run_seconds=run_seconds
            )

        lease = await attempt()
        if lease is not None:
            return lease
        if on_wait is not None:
            on_wait()
        deadline = self._clock() + max(wait_seconds, 0.0)
        while (remaining := deadline - self._clock()) > 0:
            await self._sleep(min(self._poll, remaining))
            lease = await attempt()
            if lease is not None:
                return lease
        logger.warning("local_slot_wait_expired", job_id=job_id, agent_type=agent_type, wait_seconds=wait_seconds)
        return None

    async def release(self, lease_id: str) -> None:
        """Give the slot back. Never raises: an unreleased lease expires by itself."""
        try:
            await self._leases.release(lease_id)
        except Exception:  # noqa: BLE001
            logger.warning("local_slot_release_failed", lease_id=lease_id, exc_info=True)
