"""The ask loop reads the ``asks`` section at the top of every tick, and does nothing while asks are off.

No database: the loop is built with a callable that returns the section of a
snapshot, as ``scoped_reader`` is in production. The end-to-end case, a PUT
through the API reaching the next tick, is ``tests/unit/api/test_asks_settings.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from embry0.services.ask_loop import AskLoop
from embry0.settings.registry import Section
from embry0.settings.sections import CATALOG
from tests.fixtures.asks import asks_read

# Tuesday 2026-10-27, 10:00 in Chicago.
NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


def _asks() -> Section[Any]:
    section = CATALOG.get("asks")
    assert section is not None, "asks is a section of the settings document"
    return section


def _section(**values: Any) -> Any:
    return asks_read(_asks().model(timezone="America/Chicago", **values))


class FakeAsks:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    async def live(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows if r["state"] in ("queued", "sent", "reminded")]

    async def expired_pairs(self) -> dict[tuple[str, str], Any]:
        return {
            (r["decision_id"], r["recipient"]): None for r in self.rows if r["state"] == "expired" and r.get("sent_at")
        }

    async def code(self, decision_id: str) -> str:
        return "ABCD"

    async def add(self, *, decision_id: str, recipient: str, due_at: datetime) -> str | None:
        ask_id = f"a{len(self.rows) + 1}"
        row = {"id": ask_id, "decision_id": decision_id, "recipient": recipient, "state": "queued"}
        self.rows.append({**row, "due_at": due_at, "created_at": NOW})
        return ask_id

    async def queue(self, asks: Any) -> list[str]:
        """As the repository does: each decision gets its code, then each ask its person holds no live one of."""
        queued = []
        for decision_id, recipient, due_at in asks:
            await self.code(decision_id)
            if not any(r["decision_id"] == decision_id and r["recipient"] == recipient for r in await self.live()):
                queued.append(await self.add(decision_id=decision_id, recipient=recipient, due_at=due_at))
        return [q for q in queued if q]

    async def close(self, ask_ids: Any, *, state: str, now: datetime) -> int:
        for r in self.rows:
            if r["id"] in set(ask_ids):
                r["state"] = state
        return len(set(ask_ids))


async def _people() -> dict[str, str]:
    return {"ada": "ada@example.com"}


@pytest.mark.asyncio
async def test_while_asks_are_off_a_tick_reads_and_changes_nothing() -> None:
    month_old = {
        "id": "a1",
        "decision_id": "d1",
        "recipient": "ada@example.com",
        "state": "sent",
        "due_at": NOW,
        "created_at": NOW - timedelta(days=30),
    }
    asks = FakeAsks([month_old])
    read: list[str] = []
    off = _section(enabled=False)

    async def section() -> Any:
        return off

    async def decisions() -> list[dict[str, Any]]:
        read.append("decisions")
        return [{"id": "d2", "owners": ["ada"]}]

    loop = AskLoop(settings=section, asks=asks, decisions=decisions, people=_people, clock=lambda: NOW)
    assert await loop.tick() == {"expired": 0, "withdrawn": 0, "queued": 0}
    assert read == [], "no decision is read while asks are off"
    assert [r["state"] for r in asks.rows] == ["sent"], "a stored ask is neither expired nor withdrawn while off"


@pytest.mark.asyncio
async def test_each_tick_reads_the_section_again() -> None:
    """The same loop, the section changed between two ticks: no restart."""
    asks = FakeAsks()
    current = {"section": _section(enabled=False)}

    async def section() -> Any:
        return current["section"]

    async def decisions() -> list[dict[str, Any]]:
        return [{"id": "d1", "owners": ["ada"]}]

    loop = AskLoop(settings=section, asks=asks, decisions=decisions, people=_people, clock=lambda: NOW)
    assert (await loop.tick())["queued"] == 0
    current["section"] = _section(enabled=True)
    assert (await loop.tick())["queued"] == 1, "turned on: the next tick queues"
    asks.rows[0].update(state="sent", sent_at=NOW - timedelta(days=2), created_at=NOW - timedelta(days=2))
    assert (await loop.tick())["expired"] == 0, "sent two days ago, inside expire_days 7"
    current["section"] = _section(enabled=True, expire_days=1)
    assert (await loop.tick())["expired"] == 1, "expire_days 1: the next tick expires it"


@pytest.mark.asyncio
async def test_an_ask_whose_decision_left_the_open_list_is_withdrawn_never_expired() -> None:
    """Expiring it would keep its person from being asked again when the decision comes back.

    acme's asks wait 14 days; the deployment's 7. A 10-day-old acme ask whose
    decision is missing from one tick's list is past the deployment's expiry
    and within acme's, but it is not expired by either: it is withdrawn.
    """
    section = asks_read(
        {"enabled": True, "hours": "00-24", "weekdays": "mon-sun", "expire_days": 7}, {"acme": {"expire_days": 14}}
    )
    ten_days_old = {
        "id": "a1",
        "decision_id": "d-acme",
        "recipient": "ada@example.com",
        "state": "sent",
        "due_at": NOW - timedelta(days=10),
        "created_at": NOW - timedelta(days=10),
    }
    asks = FakeAsks([ten_days_old])
    listed: list[dict[str, Any]] = []

    async def read() -> Any:
        return section

    async def decisions() -> list[dict[str, Any]]:
        return list(listed)

    loop = AskLoop(settings=read, asks=asks, decisions=decisions, people=_people, clock=lambda: NOW)
    first = await loop.tick()
    assert asks.rows[0]["state"] == "withdrawn", (
        f"its decision is not open this tick, so the ask is withdrawn, not expired: {asks.rows[0]['state']}"
    )
    assert first == {"expired": 0, "withdrawn": 1, "queued": 0}, first

    listed.append({"id": "d-acme", "repo": "acme/widgets", "owners": ["ada"]})
    assert (await loop.tick())["queued"] == 1, "the decision came back, and its person is asked again"
