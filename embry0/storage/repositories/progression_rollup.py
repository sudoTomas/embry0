"""The progression rollup's tables (#176, migration 149): what a fold over the closed days carried.

A fold is kept per scope and pack hash. The scope is whose work it folds:
``''`` the deployment's, or a digest of a set of repositories (``repos``), the
ones a key with an allowlist reads (:func:`scope_key`).
``progression_scope_rollups`` holds one row per scope and pack hash: the
version of the code that folded it, the newest closed day, the instant it
ended (the boundary), what the fold carries past it, the quest rewards it
folded, and when it was built and last rebuilt in full.
``progression_scope_daily`` holds the experience gained per day of the pack's
calendar, character and skill, and ``progression_scope_milestones`` every
level a skill reached, every character level and every rare drop, each at its
exact time and in the order the fold recorded them. Migration 131's tables
(``progression_rollups`` and the rest, one fold per pack hash) are the
previous release's, which reads and writes them if rolled back to; nothing
here touches them.

Writers serialise on one advisory lock, so two orchestrator replicas never
build at once; a reader takes one repeatable-read snapshot, so it never sees
a rollup half written. The service that folds and decides what to write is
``embry0/services/progression_rollup.py``.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import asyncpg

from embry0.storage.database import DatabasePool

#: The advisory lock every rollup writer takes, hashed with ``hashtextextended`` like the
#: other text keys registered next to ``MIGRATIONS_ADVISORY_LOCK_KEY``.
LOCK_KEY = "progression_rollup"
#: The deployment's scope: every repository's work, and work of none.
DEPLOYMENT = ""
#: A set of repositories' fold no read asked for in this long is deleted at the next full build of any scope.
UNREAD_FOR = timedelta(days=14)


def scope_key(repos: Collection[str] | None) -> str:
    """The scope a fold of ``repos`` (lower-cased slugs) is kept under: :data:`DEPLOYMENT` for None, else a digest
    of the set, so two keys covering the same repositories share one fold."""
    if repos is None:
        return DEPLOYMENT
    return "repos:" + hashlib.sha256("\n".join(sorted(set(repos))).encode()).hexdigest()[:32]


@dataclass(frozen=True)
class Milestone:
    """A level a skill reached (``level``), a character level (``agent_level``) or a rare drop (``rare``)."""

    seq: int
    at: datetime
    kind: str
    agent_type: str
    skill: str | None = None
    level: int | None = None
    rare: str | None = None


@dataclass(frozen=True)
class DailyXp:
    day: date
    agent_type: str
    skill: str
    xp: int


@dataclass(frozen=True)
class RollupHead:
    """The rollup's row: what it covers and what the fold carried past it."""

    pack_hash: str
    #: The code that folded it (``progression_rollup.FOLD_VERSION``): another release's rollup is not read.
    fold_version: str
    timezone: str
    #: The newest day of the pack's calendar folded in.
    through_day: date
    #: The instant that day ended: every event before it is folded in, none after.
    boundary: datetime
    #: ``{"characters": [...], "agent_levels": {agent: level}, "last_run_year": int | None}``.
    state: dict[str, Any]
    #: The quest rewards folded in, in order: ``[{"at", "agent_type", "stat", "xp"}]``.
    rewards: list[dict[str, Any]]
    built_at: datetime
    rebuilt_at: datetime
    #: Whose work it folds (:func:`scope_key`), and for a set of repositories, which (lower-cased slugs).
    scope: str = DEPLOYMENT
    repos: tuple[str, ...] | None = None


@dataclass(frozen=True)
class StoredRollup:
    head: RollupHead
    #: The experience per (character, skill), added up over the days.
    xp: dict[tuple[str, str], int]
    #: Every milestone, in the order the fold recorded it.
    milestones: list[Milestone]


_HEAD_COLUMNS = (
    "scope, pack_hash, repos, fold_version, timezone, through_day, boundary, state, rewards, built_at, rebuilt_at"
)


def _head(row: Any) -> RollupHead:
    return RollupHead(
        pack_hash=str(row["pack_hash"]),
        fold_version=str(row["fold_version"]),
        timezone=str(row["timezone"]),
        through_day=row["through_day"],
        boundary=row["boundary"],
        state=dict(row["state"]),
        rewards=list(row["rewards"]),
        built_at=row["built_at"],
        rebuilt_at=row["rebuilt_at"],
        scope=str(row["scope"]),
        repos=None if row["repos"] is None else tuple(row["repos"]),
    )


async def _load(conn: asyncpg.Connection, pack_hash: str, scope: str) -> StoredRollup | None:
    row = await conn.fetchrow(
        f"SELECT {_HEAD_COLUMNS} FROM progression_scope_rollups WHERE scope = $1 AND pack_hash = $2", scope, pack_hash
    )
    if row is None:
        return None
    sums = await conn.fetch(
        "SELECT agent_type, skill, sum(xp)::bigint AS xp FROM progression_scope_daily WHERE scope = $1 AND pack_hash = $2 "
        "GROUP BY agent_type, skill",
        scope,
        pack_hash,
    )
    milestones = await conn.fetch(
        "SELECT seq, at, kind, agent_type, skill, level, rare FROM progression_scope_milestones "
        "WHERE scope = $1 AND pack_hash = $2 ORDER BY seq",
        scope,
        pack_hash,
    )
    return StoredRollup(
        head=_head(row),
        xp={(str(r["agent_type"]), str(r["skill"])): int(r["xp"]) for r in sums},
        milestones=[
            Milestone(int(m["seq"]), m["at"], str(m["kind"]), str(m["agent_type"]), m["skill"], m["level"], m["rare"])
            for m in milestones
        ],
    )


async def _insert_rows(
    conn: asyncpg.Connection, head: RollupHead, daily: Sequence[DailyXp], milestones: Sequence[Milestone]
) -> None:
    if daily:
        await conn.execute(
            """
            INSERT INTO progression_scope_daily (scope, pack_hash, day, agent_type, skill, xp)
            SELECT $1, $2, d.day, d.agent_type, d.skill, d.xp
            FROM unnest($3::date[], $4::text[], $5::text[], $6::bigint[]) AS d(day, agent_type, skill, xp)
            ON CONFLICT (scope, pack_hash, day, agent_type, skill) DO UPDATE SET xp = progression_scope_daily.xp + EXCLUDED.xp
            """,
            head.scope,
            head.pack_hash,
            [d.day for d in daily],
            [d.agent_type for d in daily],
            [d.skill for d in daily],
            [d.xp for d in daily],
        )
    if milestones:
        await conn.execute(
            """
            INSERT INTO progression_scope_milestones (scope, pack_hash, seq, at, kind, agent_type, skill, level, rare)
            SELECT $1, $2, m.seq, m.at, m.kind, m.agent_type, m.skill, m.level, m.rare
            FROM unnest($3::int[], $4::timestamptz[], $5::text[], $6::text[], $7::text[], $8::int[], $9::text[])
                 AS m(seq, at, kind, agent_type, skill, level, rare)
            """,
            head.scope,
            head.pack_hash,
            [m.seq for m in milestones],
            [m.at for m in milestones],
            [m.kind for m in milestones],
            [m.agent_type for m in milestones],
            [m.skill for m in milestones],
            [m.level for m in milestones],
            [m.rare for m in milestones],
        )


class RollupWriter:
    """Writes under the rollup's advisory lock, inside one transaction: a reader sees all of it or none."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def load(self, pack_hash: str, scope: str = DEPLOYMENT) -> StoredRollup | None:
        """The rollup as it stands, read under the lock: no other writer can be changing it."""
        return await _load(self._conn, pack_hash, scope)

    async def replace(self, head: RollupHead, daily: Sequence[DailyXp], milestones: Sequence[Milestone]) -> None:
        """A full build: the scope's rollups of any pack are deleted with their rows, and this one written. Every
        other scope's rollup of another pack goes too, and a set of repositories' fold no read has advanced in
        :data:`UNREAD_FOR`: what is kept is bounded by the sets read lately."""
        await self._conn.execute(
            "DELETE FROM progression_scope_rollups WHERE scope = $1 OR pack_hash <> $2 OR (scope <> $3 AND built_at < $4)",
            head.scope,
            head.pack_hash,
            DEPLOYMENT,
            head.built_at - UNREAD_FOR,
        )
        await self._conn.execute(
            f"INSERT INTO progression_scope_rollups ({_HEAD_COLUMNS})"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10, $11)",
            head.scope,
            head.pack_hash,
            None if head.repos is None else list(head.repos),
            head.fold_version,
            head.timezone,
            head.through_day,
            head.boundary,
            head.state,
            head.rewards,
            head.built_at,
            head.rebuilt_at,
        )
        await _insert_rows(self._conn, head, daily, milestones)

    async def append(self, head: RollupHead, daily: Sequence[DailyXp], milestones: Sequence[Milestone]) -> None:
        """An advance: the row moves to ``head`` and the newly closed days' rows are added."""
        status = await self._conn.execute(
            """
            UPDATE progression_scope_rollups
               SET through_day = $3, boundary = $4, state = $5::jsonb, rewards = $6::jsonb, built_at = $7
             WHERE scope = $1 AND pack_hash = $2
            """,
            head.scope,
            head.pack_hash,
            head.through_day,
            head.boundary,
            head.state,
            head.rewards,
            head.built_at,
        )
        if status != "UPDATE 1":
            raise RuntimeError(f"no rollup for pack {head.pack_hash} in scope {head.scope!r} to advance")
        await _insert_rows(self._conn, head, daily, milestones)


class ProgressionRollupRepository:
    """The rollup tables: one consistent read, and writes serialised across processes."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def version(self, pack_hash: str, scope: str = DEPLOYMENT) -> str | None:
        """The fold version of the rollup of ``scope`` for ``pack_hash``, or None when none is built."""
        found = await self._db.fetchval(
            "SELECT fold_version FROM progression_scope_rollups WHERE scope = $1 AND pack_hash = $2", scope, pack_hash
        )
        return None if found is None else str(found)

    async def load(self, pack_hash: str, scope: str = DEPLOYMENT) -> StoredRollup | None:
        """The rollup of ``scope`` for ``pack_hash``, read in one snapshot, or None when none is built."""
        if self._db.pool is None:
            raise RuntimeError("Call connect() first")
        async with self._db.pool.acquire() as conn:
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                return await _load(conn, pack_hash, scope)

    @asynccontextmanager
    async def writing(self) -> AsyncIterator[RollupWriter]:
        """A transaction holding the rollup's advisory lock: a second writer waits for the first to commit."""
        async with self._db.transaction() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", LOCK_KEY)
            yield RollupWriter(conn)
