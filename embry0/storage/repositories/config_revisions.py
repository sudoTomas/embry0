"""Every change to a settings document (migration 128, spec 2026-10-28).

One row per write that changed a document's text: the scope and surface it
touched, the stored document before and after, who made it and which path
it came through. The settings store appends inside the write's own
transaction, so a write whose revision cannot be stored does not happen.
The same rules as ``repo_config_revisions`` (ADR 0019), which keeps a
repository's test setup and preferences and is not changed by this table.
"""

from __future__ import annotations

from typing import Any, Final, Literal

import asyncpg

from embry0.storage.database import DatabasePool

RevisionSurface = Literal["settings"]
RevisionSource = Literal["person", "file", "bootstrap", "restore", "upgrade"]

KEEP_PER_SURFACE: Final = 100
"""Rows kept per scope and surface. Each append trims the older ones."""

_COLUMNS = "id, scope, surface, before, after, actor, source, reason, created_at"


class ConfigRevisionsRepository:
    """Append and read the ``config_revisions`` table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def append(
        self,
        conn: asyncpg.Connection,
        *,
        scope: str,
        surface: RevisionSurface,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        actor: str,
        source: RevisionSource,
        reason: str,
    ) -> int:
        """Record one change on the caller's connection, inside its transaction.

        Rows of the same scope and surface beyond the newest
        ``KEEP_PER_SURFACE`` are deleted in the same transaction.
        """
        revision_id = await conn.fetchval(
            "INSERT INTO config_revisions (scope, surface, before, after, actor, source, reason) "
            "VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6, $7) RETURNING id",
            scope,
            surface,
            before,
            after,
            actor,
            source,
            reason,
        )
        await conn.execute(
            """
            DELETE FROM config_revisions
            WHERE scope = $1 AND surface = $2 AND id < (
                SELECT id FROM config_revisions
                WHERE scope = $1 AND surface = $2
                ORDER BY id DESC OFFSET $3 LIMIT 1
            )
            """,
            scope,
            surface,
            KEEP_PER_SURFACE - 1,
        )
        return int(revision_id)

    async def history(self, scope: str, surface: RevisionSurface, limit: int = 50) -> list[dict[str, Any]]:
        """One scope and surface, newest first."""
        rows = await self._db.fetch(
            f"SELECT {_COLUMNS} FROM config_revisions WHERE scope = $1 AND surface = $2 ORDER BY id DESC LIMIT $3",
            scope,
            surface,
            limit,
        )
        return [dict(r) for r in rows]

    async def get(self, revision_id: int) -> dict[str, Any] | None:
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM config_revisions WHERE id = $1", revision_id)
        return dict(row) if row else None

    async def latest_on(self, conn: asyncpg.Connection, scope: str, surface: RevisionSurface) -> dict[str, Any] | None:
        """The newest revision of one scope and surface, the only one undo may reverse."""
        row = await conn.fetchrow(
            f"SELECT {_COLUMNS} FROM config_revisions WHERE scope = $1 AND surface = $2 ORDER BY id DESC LIMIT 1",
            scope,
            surface,
        )
        return dict(row) if row else None
