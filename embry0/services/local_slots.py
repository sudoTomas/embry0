"""Slots on the operator's local model servers (docs/providers.md, local models).

A server is shared hardware with a fixed capacity, so embry0 sends each at
most ``provider_config.local_max_runs`` calls at once (one by default). Every
run on the custom provider holds a slot of the server it reaches, a lease in
``local_model_leases``, from just before the run until just after it:

- a run that only prefers the local model (local-first) takes a slot only if
  one is free now, and otherwise runs on its own cloud model;
- a run that has to use it (a fallback to it, a model a person named) waits
  for a slot up to ``LOCAL_LLM_SLOT_WAIT_SECONDS`` and then fails with
  ``ERR_LOCAL_MODEL_BUSY`` without having run.

A background call outside a job (a brief, a Slop-O-Meter judgment) holds one
for the length of the call, and its tick stops when none is free
(:class:`CallSlots`).

**Which server.** A server is the custom credential a call is made with
(:func:`server_of`): its id, or :data:`ENV_SERVER` for the environment's.
While ``LOCAL_LLM_UPSTREAM`` sends every agent run on the custom provider
through the local-llm-proxy to that one root, those runs are all on
:data:`PROXY_SERVER` whatever their credential (:func:`run_server`), and so is
a background call to the same host and port (:func:`call_server`).

A lease expires by itself after the run's own time limit, so one left by a
process that died frees its slot without anyone deleting it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger(__name__)

POLL_SECONDS = 5.0

#: The server of the custom credential the environment gives (``CUSTOM_LLM_API_KEY``), which has no id.
ENV_SERVER = "env"
#: The server every agent run on the custom provider reaches while ``LOCAL_LLM_UPSTREAM`` routes them all through
#: the local-llm-proxy to that one root.
PROXY_SERVER = "env:LOCAL_LLM_UPSTREAM"
#: A run whose credential could not be told: such runs share one cap among themselves.
UNKNOWN_SERVER = ""


def server_of(credential_id: str | None) -> str:
    """The server a custom credential names: its id, or :data:`ENV_SERVER` for the environment's."""
    return credential_id or ENV_SERVER


def run_server(credential_id: str | None, *, proxy_upstream: str) -> str:
    """The server an agent run on the custom provider reaches with ``credential_id``: the local-llm-proxy's upstream
    while one is set (every run goes there), else the credential's."""
    return PROXY_SERVER if proxy_upstream else server_of(credential_id)


def call_server(credential_id: str | None, base_url: str, *, proxy_upstream: str) -> str:
    """The server a background call reaches: it calls ``base_url`` itself, so the credential's, unless that is the
    local-llm-proxy's upstream's host and port, whose slots agent runs take as :data:`PROXY_SERVER`."""
    if proxy_upstream and _address(base_url) is not None and _address(base_url) == _address(proxy_upstream):
        return PROXY_SERVER
    return server_of(credential_id)


def _address(url: str) -> tuple[str, int] | None:
    """A URL's host and port (its scheme's when it names none); None when it names no host."""
    try:
        parts = urlparse(url.strip())
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname:
        return None
    return parts.hostname.lower(), port or (443 if parts.scheme == "https" else 80)


def local_cap(provider_config: Mapping[str, Any] | None) -> int:
    """How many calls each server takes at once: ``provider_config.local_max_runs``, else one."""
    cap = (provider_config or {}).get("local_max_runs")
    return cap if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0 else 1


class _Leases(Protocol):
    async def try_take(
        self, *, cap: int, server: str, job_id: str, agent_type: str, model: str, run_seconds: int
    ) -> str | None: ...

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

    async def try_acquire(
        self, *, cap: int, server: str, job_id: str, agent_type: str, model: str, run_seconds: int
    ) -> str | None:
        """A lease on one of ``server``'s slots if one is free now, else None. A failed read counts as no slot."""
        try:
            return await self._leases.try_take(
                cap=cap, server=server, job_id=job_id, agent_type=agent_type, model=model, run_seconds=run_seconds
            )
        except Exception:  # noqa: BLE001 - no lease, no run on the shared server
            logger.warning("local_slot_take_failed", job_id=job_id, agent_type=agent_type, exc_info=True)
            return None

    async def acquire(
        self,
        *,
        cap: int,
        server: str,
        job_id: str,
        agent_type: str,
        model: str,
        run_seconds: int,
        wait_seconds: float,
        on_wait: Callable[[], None] | None = None,
    ) -> str | None:
        """A lease id once one of ``server``'s slots is free, or None when none was within ``wait_seconds``.

        ``on_wait`` runs once, when the first try finds every slot taken.
        """

        async def attempt() -> str | None:
            return await self.try_acquire(
                cap=cap, server=server, job_id=job_id, agent_type=agent_type, model=model, run_seconds=run_seconds
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
        logger.warning(
            "local_slot_wait_expired", job_id=job_id, agent_type=agent_type, server=server, wait_seconds=wait_seconds
        )
        return None

    async def release(self, lease_id: str) -> None:
        """Give the slot back. Never raises: an unreleased lease expires by itself."""
        try:
            await self._leases.release(lease_id)
        except Exception:  # noqa: BLE001
            logger.warning("local_slot_release_failed", lease_id=lease_id, exc_info=True)


@dataclass(frozen=True)
class CallSlots:
    """What a background call needs to hold a slot of the server it reaches (``BackgroundCalls``).

    ``cap`` reads how many calls each server takes now, and raises when it
    cannot tell (no slot, then); ``proxy_upstream`` is ``LOCAL_LLM_UPSTREAM``,
    whose host agent runs reach as :data:`PROXY_SERVER`.
    """

    slots: LocalSlots
    cap: Callable[[], Awaitable[int]]
    proxy_upstream: str = ""
