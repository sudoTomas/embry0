"""Environment variables repository — global + per-repo PostgreSQL storage."""

from __future__ import annotations

from typing import Any

import structlog

from athanor.storage.database import DatabasePool

logger = structlog.get_logger(__name__)


class EnvironmentRepository:
    """CRUD for global_environment + repo_environment tables.

    Values are stored as-is (caller handles encryption for secrets; repository
    is storage-only and never looks at var_type for encryption decisions).
    """

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def get_global(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch("SELECT key, value, var_type, description FROM global_environment ORDER BY key")
        return [dict(r) for r in rows]

    async def set_global(self, variables: list[dict[str, Any]]) -> None:
        """Replace ALL global env vars with the given list (delete + insert in one txn)."""
        async with self._db.transaction() as conn:
            await conn.execute("DELETE FROM global_environment")
            for v in variables:
                await conn.execute(
                    """
                    INSERT INTO global_environment (key, value, var_type, description)
                    VALUES ($1, $2, $3, $4)
                    """,
                    v["key"],
                    v["value"],
                    v.get("var_type", "config"),
                    v.get("description", ""),
                )

    async def get_repo(self, repo: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT repo, key, value, var_type, description, required "
            "FROM repo_environment WHERE repo = $1 ORDER BY key",
            repo,
        )
        return [dict(r) for r in rows]

    async def set_repo(self, repo: str, variables: list[dict[str, Any]]) -> None:
        """Replace ALL repo env vars for a given repo (delete + insert in one txn)."""
        async with self._db.transaction() as conn:
            await conn.execute("DELETE FROM repo_environment WHERE repo = $1", repo)
            for v in variables:
                await conn.execute(
                    """
                    INSERT INTO repo_environment (repo, key, value, var_type, description, required)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    repo,
                    v["key"],
                    v["value"],
                    v.get("var_type", "config"),
                    v.get("description", ""),
                    bool(v.get("required", False)),
                )

    async def delete_repo_var(self, repo: str, key: str) -> None:
        await self._db.execute("DELETE FROM repo_environment WHERE repo = $1 AND key = $2", repo, key)
