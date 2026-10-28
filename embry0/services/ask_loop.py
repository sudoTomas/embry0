"""The ask loop (spec 2026-10-27): keeps the asks in step with the decisions waiting on people.

Each tick reads the ``asks`` section of the settings document first, so a
change applies on the next tick with no restart. While ``enabled`` is false a
tick does nothing. Otherwise it expires the asks past ``expire_days``,
withdraws the ones whose decision was answered, moved on or was handed to
someone else, and queues one for each person a decision now waits on, due at
the next working hour. A person whose ask about a decision expired is not
asked about it again: the decision stays on their page. The loop sleeps the
section's ``tick_seconds`` between ticks.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

from embry0.services.asks import AskSettings, ask_settings, expired, next_working_time, plan
from embry0.settings.sections.asks import AsksSettings

logger = structlog.get_logger(__name__)


class _Asks(Protocol):
    async def live(self) -> list[dict[str, Any]]: ...
    async def expired_pairs(self) -> set[tuple[str, str]]: ...
    async def code(self, decision_id: str) -> str: ...
    async def add(self, *, decision_id: str, recipient: str, due_at: datetime) -> str | None: ...
    async def close(self, ask_ids: Sequence[str], *, state: str, now: datetime) -> int: ...


@dataclass
class AskLoop:
    #: The ``asks`` section, read at the top of every tick: ``section_reader(store, ASKS)`` in production.
    settings: Callable[[], Awaitable[AsksSettings]]
    asks: _Asks
    #: The open decisions, with owners, held and assigned_to (the decisions API's shape).
    decisions: Callable[[], Awaitable[list[dict[str, Any]]]]
    #: GitHub login (lowercased) to the email of the person who named it.
    people: Callable[[], Awaitable[Mapping[str, str]]]
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    #: Whether the last tick found asks on; None before the first.
    _enabled: bool | None = field(default=None, init=False, repr=False)

    async def tick(self, section: AsksSettings | None = None) -> dict[str, int]:
        """One pass with the policy read now (or ``section``, read by the caller this tick).

        While asks are off it reads and changes nothing.
        """
        if section is None:
            section = await self.settings()
        if section.enabled != self._enabled:
            logger.info("asks_on" if section.enabled else "asks_off", reason="asks.enabled in the settings document")
            self._enabled = section.enabled
        if not section.enabled:
            return {"expired": 0, "withdrawn": 0, "queued": 0}
        return await self._pass(ask_settings(section))

    async def _pass(self, settings: AskSettings) -> dict[str, int]:
        now = self.clock()
        live = await self.asks.live()
        old = [str(a["id"]) for a in live if expired(a, now, settings)]
        if old:
            await self.asks.close(old, state="expired", now=now)
        live = [a for a in live if str(a["id"]) not in set(old)]
        decisions = await self.decisions()
        done_with = await self.asks.expired_pairs()
        changes = plan(decisions, live, await self.people())
        if changes.withdraw:
            await self.asks.close(changes.withdraw, state="withdrawn", now=now)
        due = next_working_time(now, settings)
        queued = 0
        for decision_id, recipient in changes.ask:
            if (decision_id, recipient) in done_with:
                continue
            await self.asks.code(decision_id)
            if await self.asks.add(decision_id=decision_id, recipient=recipient, due_at=due):
                queued += 1
        if old or changes.withdraw or queued:
            logger.info("asks_tick", expired=len(old), withdrawn=len(changes.withdraw), queued=queued)
        return {"expired": len(old), "withdrawn": len(changes.withdraw), "queued": queued}

    async def start(self) -> None:  # pragma: no cover - exercised by the app
        """Tick for ever, whether asks are on or off, so turning them on needs no restart."""
        seconds = AsksSettings().tick_seconds
        while True:
            try:
                section = await self.settings()
                seconds = section.tick_seconds
                await self.tick(section)
            except Exception:  # noqa: BLE001 - the loop goes on
                logger.warning("asks_tick_failed", exc_info=True)
            await asyncio.sleep(seconds)
