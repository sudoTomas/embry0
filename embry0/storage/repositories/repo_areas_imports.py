"""The ledger of the one-time import of each repository's old areas file (migration 145, spec 2026-11-10).

Migration 145 lists, as ``pending``, every repository registered when it
ran; the import reads only those. Each row ends ``imported`` (the file was
stored), ``none`` (nothing to import, or a document was stored already) or
``failed`` (the read failed; a later pass tries again, up to
:data:`MAX_ATTEMPTS` reads). A repository registered later has no row and is
never read. Only a row still ``pending`` or ``failed`` changes, so once one
orchestrator recorded how a repository's import ended, another that read it
at the same time changes nothing.
"""

from __future__ import annotations

from typing import Any, Final, Literal

import asyncpg

from embry0.storage.database import DatabasePool

ImportOutcome = Literal["imported", "none", "failed"]

#: How many times a repository whose read failed is read before the import stops trying.
MAX_ATTEMPTS: Final = 24

_COLUMNS = "repo, outcome, detail, blob_sha, attempts, updated_at"


class RepoAreasImportsRepository:
    """Reads and writes the ``repo_areas_imports`` table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def due(self) -> list[str]:
        """The repositories the import has still to read: ``pending``, or ``failed`` fewer than
        :data:`MAX_ATTEMPTS` times."""
        rows = await self._db.fetch(
            "SELECT repo FROM repo_areas_imports "
            "WHERE outcome = 'pending' OR (outcome = 'failed' AND attempts < $1) ORDER BY repo",
            MAX_ATTEMPTS,
        )
        return [str(r["repo"]) for r in rows]

    async def get(self, repo: str) -> dict[str, Any] | None:
        """The ledger row of ``repo``, without regard to case, or None."""
        row = await self._db.fetchrow(
            f"SELECT {_COLUMNS} FROM repo_areas_imports WHERE lower(repo) = lower($1) ORDER BY repo = $1 DESC LIMIT 1",
            repo,
        )
        return dict(row) if row else None

    async def record(
        self,
        repo: str,
        outcome: ImportOutcome,
        *,
        detail: str = "",
        blob_sha: str | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> bool:
        """How reading ``repo`` ended, each attempt counted, while its row is still ``pending`` or ``failed``;
        False when it had ended already. On ``conn``, inside the caller's transaction, when given: an import
        records ``imported`` in the transaction that stores the document."""
        recorded = await (conn or self._db).fetchval(
            "UPDATE repo_areas_imports SET outcome = $2, detail = $3, blob_sha = $4, attempts = attempts + 1, "
            "updated_at = NOW() WHERE repo = $1 AND outcome IN ('pending', 'failed') RETURNING repo",
            repo,
            outcome,
            detail,
            blob_sha,
        )
        return recorded is not None
