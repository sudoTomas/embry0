"""Context config repository — global and per-repo context injection settings."""

from typing import Any

import structlog

from legion.storage.database import DatabasePool

logger = structlog.get_logger(__name__)


class ContextConfigRepository:
    """CRUD for context injection configuration."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def set_global(self, system_context: str = "", assistant_context: str = "") -> None:
        """Set or update global context."""
        await self._db.execute(
            """
            INSERT INTO context_config (id, scope, system_context, assistant_context, updated_at)
            VALUES ('global', 'global', $1, $2, NOW())
            ON CONFLICT (id) DO UPDATE SET
                system_context = EXCLUDED.system_context,
                assistant_context = EXCLUDED.assistant_context,
                updated_at = NOW()
            """,
            system_context, assistant_context,
        )

    async def get_global(self) -> dict[str, Any] | None:
        """Get global context config."""
        row = await self._db.fetchrow("SELECT * FROM context_config WHERE id = 'global'")
        return dict(row) if row else None

    async def set_repo(self, repo: str, system_context: str = "", assistant_context: str = "") -> None:
        """Set or update per-repo context."""
        repo_id = f"repo:{repo}"
        await self._db.execute(
            """
            INSERT INTO context_config (id, scope, repo, system_context, assistant_context, updated_at)
            VALUES ($1, 'repo', $2, $3, $4, NOW())
            ON CONFLICT (id) DO UPDATE SET
                system_context = EXCLUDED.system_context,
                assistant_context = EXCLUDED.assistant_context,
                updated_at = NOW()
            """,
            repo_id, repo, system_context, assistant_context,
        )

    async def get_repo(self, repo: str) -> dict[str, Any] | None:
        """Get per-repo context config."""
        row = await self._db.fetchrow("SELECT * FROM context_config WHERE id = $1", f"repo:{repo}")
        return dict(row) if row else None

    async def list_repos(self) -> list[dict[str, Any]]:
        """List all repos that have context configured."""
        rows = await self._db.fetch(
            "SELECT repo, system_context, assistant_context FROM context_config WHERE scope = 'repo' ORDER BY repo",
        )
        return [dict(r) for r in rows]

    async def delete_repo(self, repo: str) -> None:
        """Delete per-repo context configuration."""
        await self._db.execute("DELETE FROM context_config WHERE id = $1", f"repo:{repo}")

    async def get_merged(self, repo: str) -> dict[str, str]:
        """Get merged context: global defaults, repo overrides."""
        global_ctx = await self.get_global()
        repo_ctx = await self.get_repo(repo)
        result = {"system_context": "", "assistant_context": ""}
        if global_ctx:
            result["system_context"] = global_ctx.get("system_context", "") or ""
            result["assistant_context"] = global_ctx.get("assistant_context", "") or ""
        if repo_ctx:
            if repo_ctx.get("system_context"):
                result["system_context"] = repo_ctx["system_context"]
            if repo_ctx.get("assistant_context"):
                result["assistant_context"] = repo_ctx["assistant_context"]
        return result
