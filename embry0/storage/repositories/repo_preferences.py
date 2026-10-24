"""Per-repo preferences (sandbox profile override, language hint).

Every write that changes a row appends a ``repo_config_revisions`` row in the
same transaction (ADR 0019): the row before and after, without updated_at.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import structlog

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.repo_config_revisions import RepoConfigRevisionsRepository, RevisionSource

logger = structlog.get_logger(__name__)

_COLUMNS = (
    "repo, sandbox_profile, language_hint, notes, execution_mode, auth_mode, "
    "git_author_name, git_author_email, dev_checks, base_branch, verification, pr_review, owners, updated_at"
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
    "owners",
)
_CASTS = {"dev_checks": "::jsonb", "verification": "::jsonb", "pr_review": "::jsonb", "owners": "::jsonb"}


def _db_value(column: str, value: Any) -> Any:
    """The stored form of a passed value: the clear value for None."""
    if column == "notes":
        return value or ""
    if column == "dev_checks":
        return value or []
    if column in ("verification", "pr_review", "owners"):
        return value or None
    return value


def preferences_snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    """A row as its revision stores it: every column but updated_at.

    The rest is text or JSONB already decoded, so it is JSON-safe as it is.
    """
    return {k: v for k, v in row.items() if k != "updated_at"}


class RepoPreferencesRepository:
    """CRUD for the repo_preferences table.

    Each repo (``owner/name``) may have at most one row storing the sandbox
    profile override, optional language hint, git author identity override,
    the integration branch jobs target (``base_branch``), and freeform notes.
    """

    def __init__(self, db: DatabasePool) -> None:
        self._db = db
        self._revisions = RepoConfigRevisionsRepository(db)

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
        return (await self.upsert_recorded(repo, **fields))[0]

    async def upsert_recorded(
        self,
        repo: str,
        *,
        actor: str = "",
        source: RevisionSource = "person",
        reason: str = "",
        **fields: Any,
    ) -> tuple[dict[str, Any], int | None]:
        """``upsert``, and record the change. Returns the stored row and the
        new revision's id, or None when the row came out unchanged."""
        unknown = set(fields) - set(_WRITABLE)
        if unknown:
            raise TypeError(f"unknown repo preference field(s): {sorted(unknown)}")
        cols = [c for c in _WRITABLE if c in fields]
        values = [_db_value(c, fields[c]) for c in cols]
        placeholders = [f"${i}{_CASTS.get(c, '')}" for i, c in enumerate(cols, start=2)]
        assignments = [f"{c} = EXCLUDED.{c}" for c in cols] + ["updated_at = NOW()"]
        async with self._db.transaction() as conn:
            old = await conn.fetchrow(f"SELECT {_COLUMNS} FROM repo_preferences WHERE repo = $1 FOR UPDATE", repo)
            row = await conn.fetchrow(
                f"INSERT INTO repo_preferences (repo{''.join(', ' + c for c in cols)}) "
                f"VALUES ($1{''.join(', ' + p for p in placeholders)}) "
                f"ON CONFLICT (repo) DO UPDATE SET {', '.join(assignments)} "
                f"RETURNING {_COLUMNS}",
                repo,
                *values,
            )
            assert row is not None
            before = preferences_snapshot(old) if old else None
            after = preferences_snapshot(row)
            revision_id: int | None = None
            if before != after:
                revision_id = await self._revisions.append(
                    conn,
                    repo=repo,
                    surface="preferences",
                    before=before,
                    after=after,
                    actor=actor,
                    source=source,
                    reason=reason,
                )
        return dict(row), revision_id

    async def delete(self, repo: str) -> None:
        await self.delete_recorded(repo)

    async def delete_recorded(
        self, repo: str, *, actor: str = "", source: RevisionSource = "person", reason: str = ""
    ) -> int | None:
        """Remove the row and record it, pr_review and verification
        included. Returns the revision id, or None when there was no row."""
        async with self._db.transaction() as conn:
            old = await conn.fetchrow(f"SELECT {_COLUMNS} FROM repo_preferences WHERE repo = $1 FOR UPDATE", repo)
            if old is None:
                return None
            await conn.execute("DELETE FROM repo_preferences WHERE repo = $1", repo)
            return await self._revisions.append(
                conn,
                repo=repo,
                surface="preferences",
                before=preferences_snapshot(old),
                after=None,
                actor=actor,
                source=source,
                reason=reason,
            )

    async def list_all(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch(f"SELECT {_COLUMNS} FROM repo_preferences ORDER BY repo")
        return [dict(r) for r in rows]
