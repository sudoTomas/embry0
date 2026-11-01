"""Each write that changes several asks locks them in id order (#293), on a real database.

A hand-out leases the asks it picked, the ask loop's ``close()`` withdraws or
expires asks, and an answer closes every live ask of its decision. Each locks
its rows first, in id order, then writes: two of them never each hold a row
the other waits for. An UPDATE alone locks its rows in the order it scans
them, which the plan decides.

The four asks here are stored so that neither the table's order nor its
index on (decision, recipient) is their id order. Another transaction holds
one ask; while the write waits for it, the asks it locked already are the
ones before it in id order, and none after.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.asks import AsksRepository

pytestmark = pytest.mark.requires_postgres

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
DECISION = "offer:issue:1"
IDS = [f"ask-{k}" for k in range(4)]

#: Each write that changes several asks, all four of them.
WRITES: dict[str, Callable[[AsksRepository], Coroutine[Any, Any, Any]]] = {
    "hand-out": lambda asks: asks.lease_out(
        lambda _live: [(i, False) for i in IDS], now=NOW, until=NOW + timedelta(minutes=15)
    ),
    "close": lambda asks: asks.close(IDS, state="withdrawn", now=NOW),
    "answer": lambda asks: asks.answered(DECISION, answer="start", by="ada@example.com", now=NOW),
}


async def _store(db: DatabasePool) -> None:
    """The four asks of one decision, stored last id first, each to a person who sorts in the reverse order."""
    for k in reversed(range(4)):
        await db.execute(
            "INSERT INTO asks (id, decision_id, recipient, due_at, created_at) VALUES ($1, $2, $3, $4, $4)",
            IDS[k],
            DECISION,
            f"p{3 - k}@example.com",
            NOW,
        )


async def _waiting_on_a_lock(db: DatabasePool) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        waiting = await db.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
        )
        if waiting:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the write never waited for the ask held by the other transaction")


async def _locked(db: DatabasePool, ask_id: str) -> bool:
    try:
        async with db.transaction() as conn:
            await conn.execute("SELECT 1 FROM asks WHERE id = $1 FOR UPDATE NOWAIT", ask_id)
    except asyncpg.LockNotAvailableError:
        return True
    return False


@pytest.mark.parametrize("held", [0, 3], ids=["the-first-ask-held", "the-last-ask-held"])
@pytest.mark.parametrize("write", WRITES)
async def test_a_write_to_several_asks_locks_them_in_id_order(
    db_with_migrations: DatabasePool, write: str, held: int
) -> None:
    db = db_with_migrations
    await _store(db)
    asks = AsksRepository(db)
    async with db.transaction() as holder:
        await holder.execute("SELECT 1 FROM asks WHERE id = $1 FOR UPDATE", IDS[held])
        task: asyncio.Task[Any] = asyncio.create_task(WRITES[write](asks))
        await _waiting_on_a_lock(db)
        locked = [ask_id for ask_id in IDS if ask_id != IDS[held] and await _locked(db, ask_id)]
    await asyncio.wait_for(task, 10)
    assert locked == IDS[:held], (
        f"{write}: while it waited for {IDS[held]} it held {locked}; in id order it holds exactly the asks before "
        f"that one: {IDS[:held]}"
    )
