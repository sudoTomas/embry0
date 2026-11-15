"""The rollup's writers lock one scope each (``ProgressionRollupRepository.writing``).

On a real database: a writer of one scope never waits on a writer of
another, the deployment's included; a second writer of the same scope waits
for the first; and a full build lets go of another scope's stale rollup only
while no writer of that scope holds it, never waiting for it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from embry0.services.progression_rollup import EVERY, Scope
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.progression_rollup import (
    UNREAD_FOR,
    ProgressionRollupRepository,
    RollupHead,
    RollupWriter,
)

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

NOW = datetime(2026, 1, 10, tzinfo=UTC)
A, B = Scope.of(["acme/a"]), Scope.of(["acme/b"])


def _head(scope: Scope, built: datetime = NOW, pack: str = "p") -> RollupHead:
    return RollupHead(
        pack_hash=pack,
        fold_version="v",
        timezone="UTC",
        through_day=built.date(),
        boundary=built,
        state={},
        rewards=[],
        built_at=built,
        rebuilt_at=built,
        scope=scope.key,
        repos=scope.repos,
    )


@asynccontextmanager
async def _held(store: ProgressionRollupRepository, scope: Scope) -> AsyncIterator[asyncio.Event]:
    """A writer of ``scope`` holding its lock until the event it yields is set."""
    taken, release = asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        async with store.writing(scope.key):
            taken.set()
            await release.wait()

    task = asyncio.create_task(hold())
    await asyncio.wait_for(taken.wait(), 10)
    try:
        yield release
    finally:
        release.set()
        await task


async def _build(store: ProgressionRollupRepository, head: RollupHead) -> None:
    async with store.writing(head.scope) as writer:
        assert isinstance(writer, RollupWriter)
        await writer.replace(head, [], [])


async def test_a_writer_of_one_scope_never_waits_on_a_writer_of_another(db_with_migrations: DatabasePool) -> None:
    store = ProgressionRollupRepository(db_with_migrations)
    async with _held(store, A):
        await asyncio.wait_for(_build(store, _head(B)), 5)
        await asyncio.wait_for(_build(store, _head(EVERY)), 5)
        second = asyncio.create_task(_build(store, _head(A)))
        await asyncio.sleep(0.5)
        assert not second.done(), "a second writer of the same scope waits for the first"
    await asyncio.wait_for(second, 5)
    kept = {r["scope"] for r in await db_with_migrations.fetch("SELECT scope FROM progression_scope_rollups")}
    assert kept == {A.key, B.key, EVERY.key}, kept


async def test_a_full_build_lets_go_of_another_scope_s_stale_rollup_only_when_its_lock_is_free(
    db_with_migrations: DatabasePool,
) -> None:
    db = db_with_migrations
    store = ProgressionRollupRepository(db)
    await _build(store, _head(A, NOW - UNREAD_FOR - timedelta(hours=1)))
    scopes = "SELECT scope FROM progression_scope_rollups ORDER BY scope"
    async with _held(store, A):
        await asyncio.wait_for(_build(store, _head(B)), 5)
        assert [r["scope"] for r in await db.fetch(scopes)] == sorted([A.key, B.key]), (
            "a writer of the stale scope keeps it, and the build did not wait for it"
        )
    await _build(store, _head(B))
    assert [r["scope"] for r in await db.fetch(scopes)] == [B.key], "the next build lets go of it"
