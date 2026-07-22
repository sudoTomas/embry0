"""Per-repo preferences (sandbox profile override, language hint)."""

from __future__ import annotations

from typing import Any

import structlog

from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

_COLUMNS = (
    "repo, sandbox_profile, language_hint, notes, execution_mode, auth_mode, "
    "git_author_name, git_author_email, updated_at"
)


class RepoPreferencesRepository:
    """CRUD for the repo_preferences table.

    Each repo (``owner/name``) may have at most one row storing the sandbox
    profile override, optional language hint, git author identity override,
    and freeform notes.
    """

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def get(self, repo: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            f"SELECT {_COLUMNS} FROM repo_preferences WHERE repo = $1",
            repo,
        )
        return dict(row) if row else None

    async def upsert(
        self,
        repo: str,
        sandbox_profile: str | None = None,
        language_hint: str | None = None,
        notes: str = "",
        execution_mode: str | None = None,
        auth_mode: str | None = None,
        git_author_name: str | None = None,
        git_author_email: str | None = None,
    ) -> dict[str, Any]:
        await self._db.execute(
            """
            INSERT INTO repo_preferences (repo, sandbox_profile, language_hint, notes, execution_mode, auth_mode, git_author_name, git_author_email)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (repo) DO UPDATE SET
                sandbox_profile = EXCLUDED.sandbox_profile,
                language_hint = EXCLUDED.language_hint,
                notes = EXCLUDED.notes,
                execution_mode = EXCLUDED.execution_mode,
                auth_mode = EXCLUDED.auth_mode,
                git_author_name = EXCLUDED.git_author_name,
                git_author_email = EXCLUDED.git_author_email,
                updated_at = NOW()
            """,
            repo,
            sandbox_profile,
            language_hint,
            notes,
            execution_mode,
            auth_mode,
            git_author_name,
            git_author_email,
        )
        out = await self.get(repo)
        assert out is not None
        return out

    async def delete(self, repo: str) -> None:
        await self._db.execute("DELETE FROM repo_preferences WHERE repo = $1", repo)

    async def list_all(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch(f"SELECT {_COLUMNS} FROM repo_preferences ORDER BY repo")
        return [dict(r) for r in rows]
