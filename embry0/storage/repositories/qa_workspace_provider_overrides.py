"""Repository for qa_workspace_provider_overrides — per-repo provider config set via the admin UI.

Phase 5G: when a row exists for a repo, ``qa_orchestrator_node`` prefers it
over ``.embry0/qa.yaml``'s ``workspace_provider`` section. The override is a
full replacement of ``(type, config)`` rather than a partial merge — keeps the
contract the orchestrator sees identical regardless of the source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from embry0.storage.database import DatabasePool


@dataclass(frozen=True, slots=True)
class WorkspaceProviderOverride:
    """One row from qa_workspace_provider_overrides."""

    repo: str
    provider_type: str
    config: dict[str, Any]
    updated_at: datetime


class QAWorkspaceProviderOverridesRepository:
    def __init__(self, db: DatabasePool) -> None:
        # Public attribute matches the convention used by the other QA repos
        # (qa_app_results, qa_image_tags) introduced in Phase 5+.
        self.db = db

    async def list_all(self) -> list[WorkspaceProviderOverride]:
        rows = await self.db.fetch(
            "SELECT repo, provider_type, config, updated_at FROM qa_workspace_provider_overrides ORDER BY repo"
        )
        return [
            WorkspaceProviderOverride(
                repo=r["repo"],
                provider_type=r["provider_type"],
                config=dict(r["config"] or {}),
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    async def get(self, repo: str) -> WorkspaceProviderOverride | None:
        row = await self.db.fetchrow(
            "SELECT repo, provider_type, config, updated_at FROM qa_workspace_provider_overrides WHERE repo = $1",
            repo,
        )
        if row is None:
            return None
        return WorkspaceProviderOverride(
            repo=row["repo"],
            provider_type=row["provider_type"],
            config=dict(row["config"] or {}),
            updated_at=row["updated_at"],
        )

    async def upsert(
        self,
        *,
        repo: str,
        provider_type: str,
        config: dict[str, Any],
    ) -> None:
        # config is JSONB — pass the Python dict directly. The asyncpg JSONB
        # codec encodes once; pre-encoding with json.dumps would double-wrap
        # as a JSON-string-scalar (see embry0/storage/database.py).
        await self.db.execute(
            """INSERT INTO qa_workspace_provider_overrides
                  (repo, provider_type, config, updated_at)
               VALUES ($1, $2, $3::jsonb, NOW())
               ON CONFLICT (repo) DO UPDATE SET
                  provider_type = EXCLUDED.provider_type,
                  config = EXCLUDED.config,
                  updated_at = NOW()""",
            repo,
            provider_type,
            dict(config),
        )

    async def delete(self, repo: str) -> bool:
        """Delete the override row for ``repo``.

        Returns True iff a row was actually removed; False when no override
        existed (lets the API translate to a 404).
        """
        # ``RETURNING repo`` is the codebase convention for confirming a
        # mutation row-by-row (see e.g. agent_definitions.delete_by_id);
        # avoids parsing asyncpg's "DELETE N" command-tag string.
        row = await self.db.fetchrow(
            "DELETE FROM qa_workspace_provider_overrides WHERE repo = $1 RETURNING repo",
            repo,
        )
        return row is not None
