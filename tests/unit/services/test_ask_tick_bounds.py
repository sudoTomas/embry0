"""A tick stays linear in the open decisions and the live asks, now that none is cut off at 500 (#273).

Measured by operations, not by the clock, so a loaded machine cannot fail or
pass it: each check counts the sets a step builds, which must not grow with
the number of decisions. Comparing each ask against a set rebuilt for it,
as ``plan()`` and the loop's expiry did, builds one set per ask.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from embry0.services import ask_loop, asks
from embry0.services.ask_loop import AskLoop
from embry0.services.asks import plan
from tests.fixtures.asks import asks_read

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


class CountingSet(set[Any]):
    """``set`` as the module under test calls it, counting each one it builds."""

    built = 0

    def __init__(self, *args: Any) -> None:
        CountingSet.built += 1
        super().__init__(*args)


def _sets_built(monkeypatch: pytest.MonkeyPatch, module: Any) -> type[CountingSet]:
    CountingSet.built = 0
    monkeypatch.setattr(module, "set", CountingSet, raising=False)
    return CountingSet


def _plan(n: int) -> Any:
    decisions = [{"id": f"d{i}", "owners": ["ada"]} for i in range(n)]
    live = [{"id": f"a{i}", "decision_id": f"d{i}", "recipient": "ada@example.com"} for i in range(n)]
    return plan(decisions, live, {"ada": "ada@example.com"})


@pytest.mark.parametrize("n", [10, 20_000])
def test_planning_a_tick_builds_as_many_sets_for_20000_decisions_as_for_ten(
    monkeypatch: pytest.MonkeyPatch, n: int
) -> None:
    counted = _sets_built(monkeypatch, asks)
    p = _plan(10)
    for_ten = counted.built
    counted.built = 0
    p = _plan(n)
    assert p.ask == [] and p.withdraw == [], "every open decision has its ask: nothing to queue or withdraw"
    assert counted.built == for_ten, (
        f"planning over {n} open decisions built {counted.built} sets, over ten {for_ten}: one per ask grows "
        "quadratically with the open decisions"
    )


class Asks:
    """Live asks all sent ``days`` ago, unanswered."""

    def __init__(self, n: int, days: int) -> None:
        sent = NOW - timedelta(days=days)
        self.rows = [
            {
                "id": f"a{i}",
                "decision_id": f"d{i}",
                "recipient": "ada@example.com",
                "state": "sent",
                "due_at": sent,
                "created_at": sent,
                "sent_at": sent,
                "reminders": 1,
            }
            for i in range(n)
        ]

    async def live(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows if r["state"] in ("queued", "sent", "reminded")]

    async def expired_pairs(self) -> dict[tuple[str, str], Any]:
        """As the repository answers: each pair an expired ask keeps from being asked again; None, for good."""
        return {(r["decision_id"], r["recipient"]): None for r in self.rows if r["state"] == "expired"}

    async def queue(self, new: Any) -> list[str]:
        return []

    async def close(self, ask_ids: Any, *, state: str, now: datetime) -> int:
        closing = frozenset(ask_ids)
        for r in self.rows:
            if r["id"] in closing:
                r["state"] = state
        return len(closing)


async def _expiring_tick(n: int) -> dict[str, int]:
    rows = Asks(n, days=8)

    async def section() -> Any:
        return asks_read({"enabled": True, "hours": "00-24", "weekdays": "mon-sun", "expire_days": 7})

    async def decisions() -> list[dict[str, Any]]:
        return [{"id": f"d{i}", "owners": ["ada"]} for i in range(n)]

    async def people() -> dict[str, str]:
        return {"ada": "ada@example.com"}

    loop = AskLoop(settings=section, asks=rows, decisions=decisions, people=people, clock=lambda: NOW)
    return await loop.tick()


@pytest.mark.asyncio
async def test_a_tick_that_expires_5000_asks_builds_as_many_sets_as_one_that_expires_ten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counted = _sets_built(monkeypatch, ask_loop)
    assert await _expiring_tick(10) == {"expired": 10, "withdrawn": 0, "queued": 0}
    for_ten = counted.built
    counted.built = 0
    assert await _expiring_tick(5_000) == {"expired": 5_000, "withdrawn": 0, "queued": 0}
    assert counted.built == for_ten, (
        f"a tick that expired 5,000 asks built {counted.built} sets in the loop, one that expired ten {for_ten}: "
        "a set of the expired asks rebuilt for each live ask grows quadratically with the asks"
    )
