"""Every change to a repo's test setup and preferences (migration 104, ADR 0019).

One row per write that changed something: the surface it touched, the stored
document before and after (None when the write created the row or removed
it), who made it and which path it came through. ``RepoQAConfigsRepository``
and ``RepoPreferencesRepository`` append inside the write's own transaction,
so a write whose revision cannot be stored does not happen either. The
history and undo routes read the rows back.
"""

from __future__ import annotations

from typing import Any, Final, Literal

import asyncpg

from embry0.storage.database import DatabasePool

RevisionSurface = Literal["qa_config", "preferences"]
RevisionSource = Literal["person", "bootstrap", "onboard", "proposal", "restore"]

KEEP_PER_SURFACE: Final = 100
"""Rows kept per repo and surface. Each append trims the older ones."""

_COLUMNS = "id, repo, surface, before, after, actor, source, reason, created_at"


class RepoConfigRevisionsRepository:
    """Append and read the ``repo_config_revisions`` table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def append(
        self,
        conn: asyncpg.Connection,
        *,
        repo: str,
        surface: RevisionSurface,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        actor: str,
        source: RevisionSource,
        reason: str,
    ) -> int:
        """Record one change on the caller's connection, inside its transaction.

        The caller's write and this row commit or roll back together. Rows
        of the same repo and surface beyond the newest ``KEEP_PER_SURFACE``
        are deleted in the same transaction.
        """
        revision_id = await conn.fetchval(
            "INSERT INTO repo_config_revisions (repo, surface, before, after, actor, source, reason) "
            "VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6, $7) RETURNING id",
            repo,
            surface,
            before,
            after,
            actor,
            source,
            reason,
        )
        await conn.execute(
            """
            DELETE FROM repo_config_revisions
            WHERE repo = $1 AND surface = $2 AND id < (
                SELECT id FROM repo_config_revisions
                WHERE repo = $1 AND surface = $2
                ORDER BY id DESC OFFSET $3 LIMIT 1
            )
            """,
            repo,
            surface,
            KEEP_PER_SURFACE - 1,
        )
        return int(revision_id)

    async def list_for_repo(self, repo: str, limit: int = 50) -> list[dict[str, Any]]:
        """Both surfaces of one repo, newest first."""
        rows = await self._db.fetch(
            f"SELECT {_COLUMNS} FROM repo_config_revisions WHERE repo = $1 ORDER BY id DESC LIMIT $2",
            repo,
            limit,
        )
        return [dict(r) for r in rows]

    async def get(self, revision_id: int) -> dict[str, Any] | None:
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM repo_config_revisions WHERE id = $1", revision_id)
        return dict(row) if row else None

    async def latest(self, repo: str, surface: RevisionSurface) -> dict[str, Any] | None:
        """The newest revision of one surface, the only one undo may reverse."""
        row = await self._db.fetchrow(
            f"SELECT {_COLUMNS} FROM repo_config_revisions WHERE repo = $1 AND surface = $2 ORDER BY id DESC LIMIT 1",
            repo,
            surface,
        )
        return dict(row) if row else None
