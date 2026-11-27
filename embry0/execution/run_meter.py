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
import copy
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Protocol

import structlog

from embry0.agents.run_charge import BootTally, Charge, RunMetering
from embry0.agents.run_close import metering_from_kept
from embry0.execution.admin_seq import next_admin_seq
from embry0.metrics.loop import NULL_METRICS, LoopMetrics
from embry0.storage.repositories.run_meter import RunMeterRepository, tally_key

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
    async def declare_run(
        self, proxy: str, sandbox_id: str, run_id: str, job_id: str | None = None, provider: str | None = None
    ) -> str: ...
    async def end_run(self, proxy: str, run_id: str, *, seq: int | None = None) -> dict[str, Any] | None: ...
    async def read_meter(self, proxy: str) -> dict[str, Any]: ...
    def declared_unordered(self, proxy: str, run_id: str) -> bool: ...
    def replay_abandoned(self, proxy: str) -> bool: ...


@dataclass
class RunWindow:
    """A run the meter is counting: where its sandbox is enrolled, and where it was declared."""

    sandbox_id: str
    run_id: str
    targets: tuple[str, ...]
    declared: dict[str, str] = field(default_factory=dict)
    #: The number each proxy's end carries, taken when the run ended (:meth:`RunMeter.number_end`).
    end_seqs: dict[str, int] = field(default_factory=dict)
    #: Per proxy the run could not be declared on: the sandbox's tallies of calls under no run there when the run
    #: began, by (boot id, tally key), or None when they could not be read either. What they grew by is the proxy's
    #: count of the run (:meth:`RunMeter.end`).
    undeclared_from: dict[str, dict[tuple[str, str], Mapping[str, Any]] | None] = field(default_factory=dict)


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


def _counted(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


#: What a call in flight in a tally carries besides its ceiling's counts.
_IN_FLIGHT_FIELDS = frozenset({"id", "model", "since"})


def _call_id(call: Mapping[str, Any]) -> int | None:
    """A call in flight's id in its proxy process; None from a proxy that predates ids."""
    value = call.get("id")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _counts(
    tally: Mapping[str, Any] | None, *, in_flight: Callable[[Mapping[str, Any]], bool]
) -> dict[str, dict[str, int]]:
    """A tally's counts by model, with each call in flight that ``in_flight`` takes counted at its ceiling (one
    request, estimated)."""
    counts: dict[str, dict[str, int]] = {}
    raw = tally.get("models") if isinstance(tally, Mapping) else None
    for model, entry in raw.items() if isinstance(raw, Mapping) else ():
        if isinstance(entry, Mapping):
            counts[str(model)] = {str(name): _counted(value) for name, value in entry.items()}
    calls = tally.get("open") if isinstance(tally, Mapping) else None
    for call in calls if isinstance(calls, list) else ():
        if not isinstance(call, Mapping) or not in_flight(call):
            continue
        into = counts.setdefault(str(call.get("model") or "unknown"), {})
        for name, value in call.items():
            if name not in _IN_FLIGHT_FIELDS:
                into[str(name)] = into.get(str(name), 0) + _counted(value)
        into["requests"] = into.get("requests", 0) + 1
        into["estimated"] = into.get("estimated", 0) + 1
    return counts


def _grown(
    window: RunWindow, now: Mapping[str, Any], before: Mapping[str, Any] | None, charged: frozenset[int]
) -> dict[str, Any]:
    """What a sandbox's tally of calls under no run grew by from ``before`` to ``now``, as a tally of the run.

    Every call in flight now is counted at its ceiling. A call in flight when the run began counts at its ceiling
    there too only when an earlier run was charged it so (``charged``, the ids this process remembers it charged):
    such a call is not this run's, whether still in flight or ended during it. Any other call in flight at the start
    (one that began between runs, one a proxy that predates ids reported, one this process does not remember
    charging) is this run's when it ends during it: more, never less. Each model's counts are what they rose by;
    where one fell (an earlier call that ended for less than its ceiling, or under another model's name than it was
    asked by), the run is given the totals' rise instead, under the model with the most requests."""
    after = _counts(now, in_flight=lambda _call: True)
    earlier = _counts(before, in_flight=lambda call: _call_id(call) in charged)
    rises = {
        model: {name: value - earlier.get(model, {}).get(name, 0) for name, value in entry.items()}
        for model, entry in after.items()
    }
    if any(value < 0 for entry in rises.values() for value in entry.values()) or set(earlier) - set(after):
        names = {name for entry in (*after.values(), *earlier.values()) for name in entry}
        total = {
            name: max(0, sum(e.get(name, 0) for e in after.values()) - sum(e.get(name, 0) for e in earlier.values()))
            for name in names
        }
        lead = max(after, key=lambda model: (after[model].get("requests", 0), model), default="unknown")
        rises = {lead: total}
    errors = _counted(now.get("errors")) - (_counted(before.get("errors")) if isinstance(before, Mapping) else 0)
    return {
        "run_id": window.run_id,
        "sandbox_id": window.sandbox_id,
        "attributed": False,
        "provider": None,
        "models": {model: entry for model, entry in rises.items() if any(entry.values())},
        "errors": max(0, errors),
        "open": [],
    }


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
        # Per (proxy, boot id, tally key) of a sandbox's calls under no run: the ids of the calls in flight a run that
        # could not be declared was charged at their ceiling when it ended. Held in memory only: a process that does
        # not remember charges again (more, never less).
        self._charged_in_flight: dict[tuple[str, str, str], frozenset[int]] = {}

    # ---- a run -----------------------------------------------------------------

    async def begin(
        self, sandbox_id: str, run_id: str, job_id: str | None = None, provider: str | None = None
    ) -> RunWindow:
        """Declare ``run_id`` on every metering proxy ``sandbox_id`` is enrolled with, on ``provider`` (its provider
        record's name). A proxy that refuses or cannot be reached is left out of ``declared``: the run's tally there
        will not be whole."""
        window = RunWindow(sandbox_id, run_id, tuple(self._proxies.metered_proxies_for(sandbox_id)))
        for proxy in window.targets:
            try:
                window.declared[proxy] = await self._proxies.declare_run(
                    proxy, sandbox_id, run_id, job_id, provider=provider
                )
            except Exception as exc:  # noqa: BLE001 — the run goes ahead; its charge allows for the gap
                self._metrics.meter_admin_failed(proxy, "declare")
                logger.warning("run_meter_declare_failed", proxy=proxy, run_id=run_id, error=str(exc))
                # Its calls there count under the sandbox's calls under no run: what those are now is where the
                # run's own begin.
                window.undeclared_from[proxy] = await self._unattributed(proxy, sandbox_id)
        return window

    async def _unattributed(self, proxy: str, sandbox_id: str) -> dict[tuple[str, str], Mapping[str, Any]] | None:
        """The sandbox's tallies of calls under no run at ``proxy``, by (boot id, tally key); None when the proxy's
        tallies cannot be read."""
        try:
            meter = await self._proxies.read_meter(proxy)
        except Exception as exc:  # noqa: BLE001 — the run is charged what the rest says
            logger.warning("run_meter_unattributed_read_failed", proxy=proxy, sandbox_id=sandbox_id, error=str(exc))
            return None
        boot = str(meter.get("boot_id") or "")
        # A copy: what a later read answers must not change where these stood.
        return {
            (boot, tally_key(t)): copy.deepcopy(dict(t))
            for t in meter.get("tallies") or ()
            if isinstance(t, Mapping) and not t.get("run_id") and t.get("sandbox_id") == sandbox_id
        }

    async def kept(self, window: RunWindow) -> RunMetering | None:
        """The proxies' account of the run from the tallies kept of it so far (``run_meter``), its end unread: what a
        run cancelled before its end is charged from (``embry0.agents.run_close``). None when none was kept, or they
        cannot be read."""
        if self._repository is None:
            return None
        try:
            rows = await self._repository.for_run(window.run_id)
        except Exception as exc:  # noqa: BLE001 — the run is charged its sandbox's figure
            logger.warning("run_meter_read_kept_failed", run_id=window.run_id, error=str(exc))
            return None
        return metering_from_kept(window.run_id, rows, targets=window.targets, declared=window.declared)

    def number_end(self, window: RunWindow) -> None:
        """Number the run's end on every proxy now, when it is decided, before any wait: the sandbox's next run is
        declared after this, so its declarations are newer than every one of these ends, and an end that lands first
        never has them refused. Idempotent; :meth:`end` calls it first, a caller that ends the run in the background
        calls it before handing the end over."""
        for proxy in window.declared:
            window.end_seqs.setdefault(proxy, next_admin_seq())

    async def end(self, window: RunWindow) -> RunMetering:
        """End the run on every proxy it was declared on, keep each proxy's final tally, and gather what is known."""
        self.number_end(window)
        finals: dict[tuple[str, str], BootTally] = {}
        unreached: list[str] = []
        unordered: list[str] = []
        for proxy in window.declared:
            if self._proxies.declared_unordered(proxy, window.run_id):
                # Declared without waiting out a replay of the proxy: it may have landed before an older run's, so
                # its tally there is not taken as whole (the run is charged at least what its sandbox reported).
                unordered.append(proxy)
            try:
                ended = await self._proxies.end_run(proxy, window.run_id, seq=window.end_seqs[proxy])
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
        grown: list[BootTally] = []
        for proxy in window.targets:
            if proxy in window.declared:
                continue
            # Never declared there: its calls counted under the sandbox's calls under no run, so what those grew by
            # during the run is that proxy's count of it (all of them, when where they stood at its start is not
            # known). Never whole, so the run is charged the larger of that and its sandbox's figure.
            now = await self._unattributed(proxy, window.sandbox_id)
            started = window.undeclared_from.get(proxy) or {}
            for (boot, counted_as), tally in sorted((now or {}).items()):
                where = (proxy, boot, counted_as)
                charged = self._charged_in_flight.pop(where, frozenset())
                grown.append(
                    BootTally(proxy, boot, _grown(window, tally, started.get((boot, counted_as)), charged), final=False)
                )
                # Every call in flight now is charged to this run at its ceiling: remembered, so the next run is not.
                ids = frozenset(
                    i
                    for call in tally.get("open") or ()
                    if isinstance(call, Mapping) and (i := _call_id(call)) is not None
                )
                if ids:
                    self._charged_in_flight[where] = ids
        return RunMetering(
            run_id=window.run_id,
            targets=window.targets,
            declared=dict(window.declared),
            tallies=(*kept.values(), *grown),
            unreached=tuple(unreached),
            unordered=tuple(unordered),
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
            stale = meter.get("stale_refused")
            self._metrics.meter_read(
                proxy,
                _unattributed_requests(tallies),
                stale if isinstance(stale, int) and not isinstance(stale, bool) else 0,
            )
        for proxy in self._proxies.metered_proxies():
            # After the reads: a read the proxy answers is what sends a given-up replay's remainder.
            self._metrics.meter_replay_abandoned(proxy, self._proxies.replay_abandoned(proxy))
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
