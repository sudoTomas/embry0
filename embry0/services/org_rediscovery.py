"""Rediscovery on a schedule (ADR 0016).

Discovery runs when a person asks for it (``POST /orgs/{owner}/discover``)
or when an organization action changed something. An owner whose
``settings.rediscover_hours`` is set is also walked again on its own: one
loop in the orchestrator wakes every :data:`TICK_SECONDS` and starts a run
for each active owner whose newest run is that many hours old, through the
function the endpoint uses. A repository created under the owner then
reaches the needs list as not registered, and one that left the listing is
proposed for deactivation, without anyone pressing Rediscover.

The schedule is anchored on the recorded runs, never on the process clock,
so a restart neither postpones a due run nor repeats a recent one. A run
that failed or was interrupted counts as an attempt, and so does a start
refused before any run exists (the owner's credential missing, not ok, or
no token set): the loop keeps the time of each owner's last refusal. Either
way a dead credential is tried once per interval, not once per tick. The
refusals live in the process, so a restart tries such an owner once more.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

from embry0.services.loop_schedule import seconds_until_next_run
from embry0.services.organizations import Organization, rediscover_hours

logger = structlog.get_logger(__name__)

#: How often the loop wakes. A wake reads the organization rows and, for the
#: owners on a schedule, their newest run, so it is cheap enough to be short.
TICK_SECONDS = 300

#: The actor the audit log records for a run the schedule started.
ACTOR = "embry0:org-rediscovery"


class _Organizations(Protocol):
    async def list_all(self, *, include_removed: bool = False) -> list[Organization]: ...


class _Runs(Protocol):
    async def running_run(self, owner: str) -> dict[str, Any] | None: ...

    async def list_runs(self, owner: str, *, limit: int = 20) -> list[dict[str, Any]]: ...


#: Starts one discovery run for an organization and returns its run row.
StartRun = Callable[[Organization], Awaitable[dict[str, Any]]]


def _when(value: Any) -> datetime | None:
    """A run timestamp as an aware datetime: the repository answers ISO strings."""
    if isinstance(value, str) and value:
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def is_due(newest: dict[str, Any] | None, *, hours: int, now: datetime, refused_at: datetime | None = None) -> bool:
    """Whether an owner whose newest discovery run is ``newest`` is due at ``now``.

    An owner with a run in progress never is. Otherwise the anchors are read
    as :func:`~embry0.services.loop_schedule.seconds_until_next_run` reads
    them: a finished run from when it finished, a failed or interrupted one
    from when it started, and ``refused_at``, when the loop's last start for
    the owner was refused before a run existed, as an attempt too. The owner
    is due ``hours`` after the newest anchor; one with none is due now.
    """
    if newest is not None and newest.get("status") == "running":
        return False
    newest = newest or {}
    finished = _when(newest.get("finished_at")) if newest.get("status") == "done" else None
    attempts = [t for t in (_when(newest.get("started_at")), refused_at) if t is not None]
    wait = seconds_until_next_run(
        now=now,
        interval_seconds=hours * 3600,
        min_delay_seconds=0,
        last_run_at=finished,
        last_attempt_at=max(attempts, default=None),
    )
    return wait <= 0


class OrgRediscoveryLoop:
    """Starts the scheduled discovery runs: :meth:`tick` is one pass, :meth:`start` the loop."""

    def __init__(
        self,
        *,
        organizations: _Organizations,
        runs: _Runs,
        start_run: StartRun,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        tick_seconds: float = TICK_SECONDS,
    ) -> None:
        self._organizations = organizations
        self._runs = runs
        self._start_run = start_run
        self._clock = clock
        self._tick_seconds = tick_seconds
        # When each owner's last start was refused before a run row existed,
        # the one attempt nothing else records.
        self._refused_at: dict[str, datetime] = {}

    async def tick(self) -> list[str]:
        """Start a run for every due owner and return the owners started.

        Only active owners with ``rediscover_hours`` set are considered. An
        owner with a run in progress is skipped whoever started it, a person
        or an earlier tick, so the schedule never runs two at once for one
        owner. One owner's failure to start (its credential gone, a start the
        repository refused because another began first) is logged and counts
        as an attempt, so that owner waits a full interval before the next
        one; the other owners still go.
        """
        now = self._clock()
        started: list[str] = []
        for org in await self._organizations.list_all():
            hours = rediscover_hours(org.settings)
            if org.status != "active" or hours is None:
                continue
            if await self._runs.running_run(org.owner) is not None:
                continue
            newest = await self._runs.list_runs(org.owner, limit=1)
            refused_at = self._refused_at.get(org.owner)
            if not is_due(newest[0] if newest else None, hours=hours, now=now, refused_at=refused_at):
                continue
            try:
                run = await self._start_run(org)
            except Exception as exc:  # noqa: BLE001 - one owner's failure is its own log line
                self._refused_at[org.owner] = now
                logger.warning(
                    "org_rediscovery_not_started",
                    owner=org.owner,
                    error=type(exc).__name__,
                    detail=str(getattr(exc, "detail", "") or "")[:200],
                    retry_in_hours=hours,
                )
                continue
            self._refused_at.pop(org.owner, None)
            started.append(org.owner)
            logger.info("org_rediscovery_started", owner=org.owner, run_id=run.get("id"), hours=hours)
        return started

    async def start(self) -> None:  # pragma: no cover - exercised by the app
        while True:
            await asyncio.sleep(self._tick_seconds)
            try:
                await self.tick()
            except Exception:
                logger.warning("org_rediscovery_tick_failed", exc_info=True)
