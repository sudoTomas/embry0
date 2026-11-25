"""The orchestrator's side of metering: each agent run is declared to the metering proxies its sandbox is enrolled
with before it starts and ended after, and every proxy's tallies are read every few seconds into ``run_meter``.

What is gathered here, :class:`embry0.agents.run_charge.RunMetering`, is what the run is charged from
(:func:`embry0.agents.run_charge.charge_run`). Every call to a proxy goes through ``ProxyManager``'s admin transport
(``docker exec`` on the proxy's loopback), which recreates a proxy found stopped and gives it back its enrollments and
its declared runs.

Nothing here stops a run: a proxy that cannot be reached leaves the run's tally not whole, and the run is charged the
larger of what was counted and what its sandbox reported.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Protocol

import structlog

from embry0.agents.run_charge import BootTally, Charge, RunMetering
from embry0.metrics.loop import NULL_METRICS, LoopMetrics
from embry0.storage.repositories.run_meter import RunMeterRepository

logger = structlog.get_logger(__name__)

#: How often every metering proxy's tallies are read.
POLL_SECONDS = 5.0
#: How long a tally is kept once last read.
RETENTION = timedelta(days=30)
#: How often tallies past :data:`RETENTION` are dropped.
PRUNE_SECONDS = 3600.0


class MeteringProxies(Protocol):
    """What the meter needs of ``ProxyManager``."""

    def metered_proxies(self) -> list[str]: ...
    def metered_proxies_for(self, sandbox_id: str) -> list[str]: ...
    async def declare_run(self, proxy: str, sandbox_id: str, run_id: str, job_id: str | None = None) -> str: ...
    async def end_run(self, proxy: str, run_id: str) -> dict[str, Any] | None: ...
    async def read_meter(self, proxy: str) -> dict[str, Any]: ...


@dataclass
class RunWindow:
    """A run the meter is counting: where its sandbox is enrolled, and where it was declared."""

    sandbox_id: str
    run_id: str
    targets: tuple[str, ...]
    declared: dict[str, str] = field(default_factory=dict)


def _unattributed_requests(tallies: list[Any]) -> int:
    total = 0
    for tally in tallies:
        if not isinstance(tally, dict) or tally.get("run_id"):
            continue
        models = tally.get("models")
        for entry in models.values() if isinstance(models, dict) else ():
            requests = entry.get("requests") if isinstance(entry, dict) else None
            total += requests if isinstance(requests, int) and not isinstance(requests, bool) else 0
    return total


class RunMeter:
    """Declares and ends runs on the metering proxies, and keeps their tallies."""

    def __init__(
        self,
        proxies: MeteringProxies,
        repository: RunMeterRepository | None,
        *,
        metrics: LoopMetrics = NULL_METRICS,
        poll_seconds: float = POLL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._proxies = proxies
        self._repository = repository
        self._metrics = metrics
        self._poll_seconds = poll_seconds
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._last_prune: float | None = None

    # ---- a run -----------------------------------------------------------------

    async def begin(self, sandbox_id: str, run_id: str, job_id: str | None = None) -> RunWindow:
        """Declare ``run_id`` on every metering proxy ``sandbox_id`` is enrolled with. A proxy that refuses or cannot
        be reached is left out of ``declared``: the run's tally there will not be whole."""
        window = RunWindow(sandbox_id, run_id, tuple(self._proxies.metered_proxies_for(sandbox_id)))
        for proxy in window.targets:
            try:
                window.declared[proxy] = await self._proxies.declare_run(proxy, sandbox_id, run_id, job_id)
            except Exception as exc:  # noqa: BLE001 — the run goes ahead; its charge allows for the gap
                self._metrics.meter_admin_failed(proxy, "declare")
                logger.warning("run_meter_declare_failed", proxy=proxy, run_id=run_id, error=str(exc))
        return window

    async def end(self, window: RunWindow) -> RunMetering:
        """End the run on every proxy it was declared on, keep each proxy's final tally, and gather what is known."""
        finals: dict[tuple[str, str], BootTally] = {}
        unreached: list[str] = []
        for proxy in window.declared:
            try:
                ended = await self._proxies.end_run(proxy, window.run_id)
            except Exception as exc:  # noqa: BLE001 — a lost end is a gap, never a failed run
                self._metrics.meter_admin_failed(proxy, "end")
                logger.warning("run_meter_end_failed", proxy=proxy, run_id=window.run_id, error=str(exc))
                unreached.append(proxy)
                continue
            if ended is None:
                unreached.append(proxy)
                continue
            boot_id, tally = str(ended.get("boot_id") or ""), ended["tally"]
            finals[(proxy, boot_id)] = BootTally(proxy, boot_id, tally, final=True)
            await self._keep(proxy, boot_id, [tally], final=True)
        kept: dict[tuple[str, str], BootTally] = {}
        if self._repository is not None:
            try:
                for row in await self._repository.for_run(window.run_id):
                    key = (str(row["proxy"]), str(row["boot_id"]))
                    kept[key] = BootTally(key[0], key[1], row["snapshot"], final=bool(row["final"]))
            except Exception as exc:  # noqa: BLE001 — what this process holds still counts
                logger.warning("run_meter_read_kept_failed", run_id=window.run_id, error=str(exc))
        kept.update(finals)
        return RunMetering(
            run_id=window.run_id,
            targets=window.targets,
            declared=dict(window.declared),
            tallies=tuple(kept.values()),
            unreached=tuple(unreached),
        )

    async def _keep(self, proxy: str, boot_id: str, tallies: list[Any], *, final: bool = False) -> None:
        if self._repository is None or not boot_id:
            return
        try:
            await self._repository.record(proxy, boot_id, tallies, final=final)
        except Exception as exc:  # noqa: BLE001 — the next read writes it again
            logger.warning("run_meter_keep_failed", proxy=proxy, error=str(exc))

    def charged(self, charge: Charge, *, run_id: str) -> None:
        """Count how a run was charged, and say so when its two figures disagree."""
        self._metrics.run_charged(charge.source, charge.disagreement)
        if charge.disagreement is not None:
            logger.warning(
                "run_meter_figures_disagree",
                run_id=run_id,
                higher=charge.disagreement,
                proxy_cost_usd=charge.metering.get("proxy_cost_usd"),
                sandbox_cost_usd=charge.sandbox_cost_usd,
                charged_usd=charge.cost_usd,
                source=charge.source,
            )

    # ---- reading every proxy -----------------------------------------------------

    async def poll_once(self) -> None:
        """Read every metering proxy's tallies and keep them."""
        for proxy in self._proxies.metered_proxies():
            try:
                meter = await self._proxies.read_meter(proxy)
            except Exception as exc:  # noqa: BLE001 — the next read tries again; the alert counts the silence
                self._metrics.meter_admin_failed(proxy, "read")
                logger.warning("run_meter_read_failed", proxy=proxy, error=str(exc))
                continue
            tallies = [t for t in meter["tallies"] if isinstance(t, dict)]
            await self._keep(proxy, meter["boot_id"], tallies)
            self._metrics.meter_read(proxy, _unattributed_requests(tallies))
        await self._prune_now_and_then()

    async def _prune_now_and_then(self) -> None:
        now = self._clock()
        if self._repository is None or (self._last_prune is not None and now - self._last_prune < PRUNE_SECONDS):
            return
        self._last_prune = now
        try:
            dropped = await self._repository.prune(RETENTION)
        except Exception as exc:  # noqa: BLE001 — tried again within the hour
            logger.warning("run_meter_prune_failed", error=str(exc))
            return
        if dropped:
            logger.info("run_meter_pruned", rows=dropped)

    def start(self) -> None:
        """Read every metering proxy every ``poll_seconds`` until :meth:`stop`."""
        self._metrics.meter_proxies(self._proxies.metered_proxies())
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="run-meter-poll")

    async def _loop(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception as exc:  # noqa: BLE001 — the loop outlives any one read
                logger.warning("run_meter_poll_failed", error=str(exc))
            await asyncio.sleep(self._poll_seconds)

    async def stop(self) -> None:
        """Stop reading, then read every proxy once more: the last of each tally is kept before the proxies go."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.poll_once()
