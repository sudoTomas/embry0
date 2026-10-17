"""org_needs: what embry0 still needs to maintain an owner (ADR 0011, migration 99).

One row per (owner, kind, repositories) so a person's answer survives
rediscovery: the same need derived again finds its row and keeps its
status and case; a need with a different set of repositories is a new row.
``org_needs_synced`` names the run each owner's needs were last synced from.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from embry0.services.org_needs import STATUSES, Need
from embry0.storage.database import DatabasePool

_COLUMNS = "id, owner, run_id, kind, signature, title, detail, repos, action, status, case_id, created_at, updated_at"


def _row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "owner": row["owner"],
        "run_id": row["run_id"],
        "kind": row["kind"],
        "signature": row["signature"],
        "title": row["title"],
        "detail": row["detail"],
        "repos": list(row["repos"] or []),
        "action": dict(row["action"] or {}),
        "status": row["status"],
        "case_id": row["case_id"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


class OrgNeedsRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    @asynccontextmanager
    async def owner_lock(self, owner: str, *, timeout: float | None = None) -> AsyncIterator[bool]:
        """Hold the owner's needs lock for the block: a Postgres advisory
        transaction lock, so syncs of one owner serialize across processes,
        not only within one. Yields whether the lock is held: with a
        ``timeout`` (seconds) it waits that long at most, then yields False,
        and the caller must not write.

        The lock's transaction holds the lock and nothing else. The block's
        queries run on other pooled connections and commit as they go, so
        the next holder sees them. A waiter holds its pooled connection, which
        is what the timeout bounds.
        """
        key = f"org_needs:{owner.lower()}"
        async with self._db.transaction() as conn:
            if timeout is not None:
                await conn.execute(f"SET LOCAL lock_timeout = '{max(1, int(timeout * 1000))}ms'")
            try:
                # A savepoint: a wait that times out rolls back only itself,
                # and a lock taken in it passes to the transaction.
                async with conn.transaction():
                    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", key)
            except asyncpg.exceptions.LockNotAvailableError:
                held = False
            else:
                held = True
            yield held

    async def synced_run(self, owner: str) -> str | None:
        """The discovery run the owner's needs were last synced from, or None."""
        run_id = await self._db.fetchval("SELECT run_id FROM org_needs_synced WHERE owner = $1", owner.lower())
        return str(run_id) if run_id is not None else None

    async def mark_synced(self, owner: str, run_id: str) -> None:
        await self._db.execute(
            """
            INSERT INTO org_needs_synced (owner, run_id) VALUES ($1, $2)
            ON CONFLICT (owner) DO UPDATE SET run_id = EXCLUDED.run_id, synced_at = NOW()
            """,
            owner.lower(),
            run_id,
        )

    async def list_for_owner(self, owner: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            f"""
            SELECT {_COLUMNS} FROM org_needs WHERE owner = $1
            ORDER BY (status = 'open') DESC, (status = 'done') DESC, created_at, id
            """,
            owner.lower(),
        )
        return [_row(r) for r in rows]

    async def get(self, need_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM org_needs WHERE id = $1", need_id)
        return _row(row) if row else None

    async def upsert(self, owner: str, need: Need, *, run_id: str | None) -> dict[str, Any]:
        """Store a derived need, or refresh the row that already stands for it.

        Keyed by (owner, kind, signature): the wording, repositories, action
        and run are rewritten from the new derivation, the status and case
        are kept, because they are a person's answer, not the forge's.
        """
        row = await self._db.fetchrow(
            f"""
            INSERT INTO org_needs (id, owner, run_id, kind, signature, title, detail, repos, action)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb)
            ON CONFLICT (owner, kind, signature) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                title = EXCLUDED.title,
                detail = EXCLUDED.detail,
                repos = EXCLUDED.repos,
                action = EXCLUDED.action,
                updated_at = NOW()
            RETURNING {_COLUMNS}
            """,
            f"need-{secrets.token_hex(6)}",
            owner.lower(),
            run_id,
            need.kind,
            need.signature,
            need.title,
            need.detail,
            list(need.repos),
            dict(need.action),
        )
        return _row(row)

    async def settle_missing(self, owner: str, keep_ids: list[str]) -> list[dict[str, Any]]:
        """Open needs of ``owner`` not in ``keep_ids`` are done: the run no longer derives them."""
        rows = await self._db.fetch(
            f"""
            UPDATE org_needs SET status = 'done', updated_at = NOW()
             WHERE owner = $1 AND status = 'open' AND NOT (id = ANY($2::text[]))
            RETURNING {_COLUMNS}
            """,
            owner.lower(),
            list(keep_ids),
        )
        return [_row(r) for r in rows]

    async def set_status(self, need_id: str, status: str) -> dict[str, Any] | None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        row = await self._db.fetchrow(
            f"UPDATE org_needs SET status = $2, updated_at = NOW() WHERE id = $1 RETURNING {_COLUMNS}",
            need_id,
            status,
        )
        return _row(row) if row else None

    async def set_case(self, need_id: str, case_id: str | None) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            f"UPDATE org_needs SET case_id = $2, updated_at = NOW() WHERE id = $1 RETURNING {_COLUMNS}",
            need_id,
            case_id,
        )
        return _row(row) if row else None
