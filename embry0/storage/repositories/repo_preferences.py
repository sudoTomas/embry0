"""Per-repo preferences (sandbox profile override, language hint)."""

from __future__ import annotations

from typing import Any

import structlog

from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

_COLUMNS = (
    "repo, sandbox_profile, language_hint, notes, execution_mode, auth_mode, "
    "git_author_name, git_author_email, dev_checks, base_branch, verification, pr_review, updated_at"
)


# Column names are interpolated into SQL, so only these may ever reach it.
_WRITABLE = (
    "sandbox_profile",
    "language_hint",
    "notes",
    "execution_mode",
    "auth_mode",
    "git_author_name",
    "git_author_email",
    "dev_checks",
    "base_branch",
    "verification",
    "pr_review",
)
_CASTS = {"dev_checks": "::jsonb", "verification": "::jsonb", "pr_review": "::jsonb"}


def _db_value(column: str, value: Any) -> Any:
    """The stored form of a passed value: the clear value for None."""
    if column == "notes":
        return value or ""
    if column == "dev_checks":
        return value or []
    if column in ("verification", "pr_review"):
        return value or None
    return value


class RepoPreferencesRepository:
    """CRUD for the repo_preferences table.

    Each repo (``owner/name``) may have at most one row storing the sandbox
    profile override, optional language hint, git author identity override,
    the integration branch jobs target (``base_branch``), and freeform notes.
    """

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def get(self, repo: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            f"SELECT {_COLUMNS} FROM repo_preferences WHERE repo = $1",
            repo,
        )
        return dict(row) if row else None

    async def upsert(self, repo: str, **fields: Any) -> dict[str, Any]:
        """Create the row, or update ONLY the fields passed (#350).

        A field left out keeps its stored value; one passed is written,
        ``None`` included, which clears it (``notes`` to ``''``,
        ``dev_checks`` to ``[]``, ``verification`` to NULL, ``pr_review`` to
        NULL; ``{}`` also clears ``verification``). Before #350 this replaced
        the whole row, so a caller naming one field wiped the rest.
        """
        unknown = set(fields) - set(_WRITABLE)
        if unknown:
            raise TypeError(f"unknown repo preference field(s): {sorted(unknown)}")
        cols = [c for c in _WRITABLE if c in fields]
        values = [_db_value(c, fields[c]) for c in cols]
        placeholders = [f"${i}{_CASTS.get(c, '')}" for i, c in enumerate(cols, start=2)]
        assignments = [f"{c} = EXCLUDED.{c}" for c in cols] + ["updated_at = NOW()"]
        await self._db.execute(
            f"INSERT INTO repo_preferences (repo{''.join(', ' + c for c in cols)}) "
            f"VALUES ($1{''.join(', ' + p for p in placeholders)}) "
            f"ON CONFLICT (repo) DO UPDATE SET {', '.join(assignments)}",
            repo,
            *values,
        )
        out = await self.get(repo)
        assert out is not None
        return out

    async def delete(self, repo: str) -> None:
        await self._db.execute("DELETE FROM repo_preferences WHERE repo = $1", repo)

    async def list_all(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch(f"SELECT {_COLUMNS} FROM repo_preferences ORDER BY repo")
        return [dict(r) for r in rows]
