"""The ledger of the one-time import of each repository's old areas file (migration 145, spec 2026-11-10).

Migration 145 listed, as ``pending``, every repository registered when it
ran, and the import that release carried recorded how each read ended:
``imported`` (the file was stored), ``none`` (nothing to import, or a
document was stored already) or ``failed`` (the read kept failing). The
import is gone (#175), so nothing writes the ledger any more: it is read for
when a repository's areas were imported (``imported`` on its areas), and for
the repositories never read (:mod:`embry0.services.areas_import_check`).
"""

from __future__ import annotations

from typing import Any

from embry0.storage.database import DatabasePool

_COLUMNS = "repo, outcome, detail, blob_sha, attempts, updated_at"


class RepoAreasImportsRepository:
    """Reads the ``repo_areas_imports`` table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def get(self, repo: str) -> dict[str, Any] | None:
        """The ledger row of ``repo``, without regard to case, or None."""
        row = await self._db.fetchrow(
            f"SELECT {_COLUMNS} FROM repo_areas_imports WHERE lower(repo) = lower($1) ORDER BY repo = $1 DESC LIMIT 1",
            repo,
        )
        return dict(row) if row else None
