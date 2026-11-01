"""Who is not asked again about a decision, and for how long (#295).

- An ask that reached its person and expired keeps them from being asked
  about that decision for good.
- An ask the feed handed out that was never reported sent expired
  ``expire_days`` after it was handed out; it keeps its person from being
  asked again for ``expire_days`` after that, then they may be asked again.
- An ask that expired without being handed out or sent, which a release
  before #295 did to an ask that waited in the queue, keeps no one from being
  asked: the next tick asks its person again, once, with nothing to rewrite.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from embry0.services.ask_loop import AskLoop
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.asks import AsksRepository
from tests.fixtures.asks import asks_read

pytestmark = pytest.mark.requires_postgres

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
#: When each expired ask closed: a day ago.
CLOSED = NOW - timedelta(days=1)


async def _row(db: DatabasePool, ask_id: str, decision_id: str, *, sent: bool, handed_out: bool) -> None:
    await db.execute(
        "INSERT INTO asks (id, decision_id, recipient, due_at, created_at, sent_at, handed_out_at, state, closed_at) "
        "VALUES ($1, $2, 'ada@example.com', $3, $3, $4, $5, 'expired', $6)",
        ask_id,
        decision_id,
        NOW - timedelta(days=9),
        NOW - timedelta(days=8) if sent else None,
        NOW - timedelta(days=8) if handed_out else None,
        CLOSED,
    )


def _loop(asks: AsksRepository, clock: list[datetime]) -> AskLoop:
    async def section() -> Any:
        return asks_read({"enabled": True, "hours": "00-24", "weekdays": "mon-sun", "expire_days": 7})

    async def decisions() -> list[dict[str, Any]]:
        return [{"id": f"offer:issue:{i}", "owners": ["ada"]} for i in (1, 2, 3)]

    async def people() -> dict[str, str]:
        return {"ada": "ada@example.com"}

    return AskLoop(settings=section, asks=asks, decisions=decisions, people=people, clock=lambda: clock[0])


async def test_who_is_kept_from_being_asked_again_and_for_how_long(db_with_migrations: DatabasePool) -> None:
    await _row(db_with_migrations, "ask-sent", "offer:issue:1", sent=True, handed_out=True)
    await _row(db_with_migrations, "ask-handed-out", "offer:issue:2", sent=False, handed_out=True)
    await _row(db_with_migrations, "ask-never-handed-out", "offer:issue:3", sent=False, handed_out=False)
    asks = AsksRepository(db_with_migrations)
    assert await asks.expired_pairs() == {
        ("offer:issue:1", "ada@example.com"): None,
        ("offer:issue:2", "ada@example.com"): CLOSED,
    }, (
        "issue 1's ask reached ada: for good; issue 2's was handed out, never reported sent: from when it expired; "
        "issue 3's never left the queue: not at all"
    )

    clock = [NOW]
    loop = _loop(asks, clock)
    assert await loop.tick() == {"expired": 0, "withdrawn": 0, "queued": 1}
    assert [a["decision_id"] for a in await asks.live()] == ["offer:issue:3"], (
        "a day after they expired, ada is asked again about issue 3, whose ask never left the queue, only"
    )

    clock[0] = CLOSED + timedelta(days=7, minutes=1)
    assert await loop.tick() == {"expired": 0, "withdrawn": 0, "queued": 1}
    assert sorted(a["decision_id"] for a in await asks.live()) == ["offer:issue:2", "offer:issue:3"], (
        "expire_days after issue 2's undelivered ask expired, ada may be asked about it again; never about issue 1"
    )
    states = {r["id"]: r["state"] for r in await db_with_migrations.fetch("SELECT id, state FROM asks")}
    assert all(states[a] == "expired" for a in ("ask-sent", "ask-handed-out", "ask-never-handed-out")), (
        "the old rows are left as they were"
    )


async def test_two_polls_that_hand_an_ask_out_at_once_keep_one_time(db_with_migrations: DatabasePool) -> None:
    """The stamp is set in the statement that hands the ask out, so concurrent polls cannot each set their own."""
    asks = AsksRepository(db_with_migrations)
    (ask_id,) = await asks.queue([("offer:issue:1", "ada@example.com", NOW)])
    assert ask_id is not None
    first, second = NOW, NOW + timedelta(seconds=1)
    results = await asyncio.gather(*(asks.hand_out([ask_id], now=t) for t in (first, second, first, second)))
    stamped = {r[ask_id] for r in results}
    stored = (await asks.get(ask_id) or {}).get("handed_out_at")
    assert len(stamped) == 1 and stamped == {stored}, (
        f"four polls at once handed the ask out: one time is kept, the same in every answer and stored: "
        f"{sorted(stamped)} stored {stored}"
    )
    assert (await asks.hand_out([ask_id], now=NOW + timedelta(days=3)))[ask_id] == stored, (
        "a later poll keeps the first time"
    )
    await asks.sent(ask_id, message_ref="m-1", now=NOW + timedelta(days=3))
    assert await asks.hand_out([ask_id], now=NOW + timedelta(days=4)) == {}, "a sent ask is not stamped again"
