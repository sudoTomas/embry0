"""Issues repository — PostgreSQL CRUD for issue tracking."""

from __future__ import annotations

import json
import uuid
from typing import Any

import structlog

from legion.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

_ALLOWED_UPDATE_FIELDS = frozenset({
    "title", "body", "status", "priority", "labels", "repo",
    "parent_issue_id", "github_number", "github_url", "github_sync_enabled",
    "github_synced_at", "created_by",
})

_ALLOWED_SORT_COLUMNS = frozenset({"created_at", "updated_at", "priority"})


class IssuesRepository:
    """CRUD operations for the issues table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def create(
        self,
        title: str,
        body: str = "",
        priority: str = "medium",
        labels: list[str] | None = None,
        repo: str | None = None,
        parent_issue_id: str | None = None,
        github_sync_enabled: bool = False,
        created_by: str = "user",
    ) -> str:
        """Create a new issue and return its ID."""
        issue_id = f"iss-{uuid.uuid4().hex[:12]}"
        if labels is None:
            labels = []
        await self._db.execute(
            """
            INSERT INTO issues (
                id, title, body, priority, labels, repo,
                parent_issue_id, github_sync_enabled, created_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            issue_id,
            title,
            body,
            priority,
            json.dumps(labels),
            repo,
            parent_issue_id,
            github_sync_enabled,
            created_by,
        )
        logger.info("issue_created", issue_id=issue_id, title=title)
        return issue_id

    async def get(self, issue_id: str) -> dict[str, Any] | None:
        """Fetch a single issue by ID, including children_count, jobs_count, and active_agent."""
        row = await self._db.fetchrow(
            """
            SELECT
                i.*,
                (SELECT COUNT(*) FROM issues c WHERE c.parent_issue_id = i.id) AS children_count,
                (SELECT COUNT(*) FROM jobs j WHERE j.issue_id = i.id) AS jobs_count,
                (SELECT j.status FROM jobs j WHERE j.issue_id = i.id AND j.status = 'running'
                 ORDER BY j.created_at DESC LIMIT 1) AS active_agent
            FROM issues i
            WHERE i.id = $1
            """,
            issue_id,
        )
        if row is None:
            return None
        return dict(row)

    async def list(
        self,
        status: str | None = None,
        priority: str | None = None,
        repo: str | None = None,
        labels: str | None = None,
        search: str | None = None,
        top_level_only: bool = False,
        sort: str = "created_at",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """List issues with optional filters. Returns (rows, total_count)."""
        conditions: list[str] = []
        args: list[Any] = []
        idx = 1

        if status:
            conditions.append(f"i.status = ${idx}")
            args.append(status)
            idx += 1
        if priority:
            conditions.append(f"i.priority = ${idx}")
            args.append(priority)
            idx += 1
        if repo:
            conditions.append(f"i.repo = ${idx}")
            args.append(repo)
            idx += 1
        if labels:
            conditions.append(f"i.labels @> ${idx}::jsonb")
            args.append(f'["{labels}"]')
            idx += 1
        if search:
            conditions.append(f"(i.title ILIKE ${idx} OR i.body ILIKE ${idx})")
            args.append(f"%{search}%")
            idx += 1
        if top_level_only:
            conditions.append("i.parent_issue_id IS NULL")

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        sort_col = sort if sort in _ALLOWED_SORT_COLUMNS else "created_at"
        sort_dir = "ASC" if order.lower() == "asc" else "DESC"

        total = await self._db.fetchval(
            f"SELECT COUNT(*) FROM issues i {where}",
            *args,
        )

        args.extend([limit, offset])
        rows = await self._db.fetch(
            f"""
            SELECT
                i.*,
                (SELECT COUNT(*) FROM issues c WHERE c.parent_issue_id = i.id) AS children_count,
                (SELECT COUNT(*) FROM jobs j WHERE j.issue_id = i.id) AS jobs_count,
                (SELECT j.status FROM jobs j WHERE j.issue_id = i.id AND j.status = 'running'
                 ORDER BY j.created_at DESC LIMIT 1) AS active_agent
            FROM issues i
            {where}
            ORDER BY i.{sort_col} {sort_dir}
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *args,
        )
        return [dict(r) for r in rows], total or 0

    async def update(self, issue_id: str, **fields: Any) -> None:
        """Update specific fields on an issue. updated_at is always refreshed."""
        valid = {k: v for k, v in fields.items() if k in _ALLOWED_UPDATE_FIELDS}
        if not valid:
            return

        sets: list[str] = ["updated_at = NOW()"]
        args: list[Any] = []
        idx = 1
        for key, value in valid.items():
            sets.append(f"{key} = ${idx}")
            args.append(value)
            idx += 1

        args.append(issue_id)
        await self._db.execute(
            f"UPDATE issues SET {', '.join(sets)} WHERE id = ${idx}",
            *args,
        )
        logger.info("issue_updated", issue_id=issue_id, fields=list(valid.keys()))

    async def get_children(self, parent_issue_id: str) -> list[dict[str, Any]]:
        """Fetch all direct children of an issue."""
        rows = await self._db.fetch(
            """
            SELECT
                i.*,
                (SELECT COUNT(*) FROM issues c WHERE c.parent_issue_id = i.id) AS children_count,
                (SELECT COUNT(*) FROM jobs j WHERE j.issue_id = i.id) AS jobs_count,
                (SELECT j.status FROM jobs j WHERE j.issue_id = i.id AND j.status = 'running'
                 ORDER BY j.created_at DESC LIMIT 1) AS active_agent
            FROM issues i
            WHERE i.parent_issue_id = $1
            ORDER BY i.created_at ASC
            """,
            parent_issue_id,
        )
        return [dict(r) for r in rows]

    async def get_by_github(self, repo: str, github_number: int) -> dict[str, Any] | None:
        """Fetch an issue by its GitHub repo and issue number."""
        row = await self._db.fetchrow(
            """
            SELECT
                i.*,
                (SELECT COUNT(*) FROM issues c WHERE c.parent_issue_id = i.id) AS children_count,
                (SELECT COUNT(*) FROM jobs j WHERE j.issue_id = i.id) AS jobs_count,
                (SELECT j.status FROM jobs j WHERE j.issue_id = i.id AND j.status = 'running'
                 ORDER BY j.created_at DESC LIMIT 1) AS active_agent
            FROM issues i
            WHERE i.repo = $1 AND i.github_number = $2
            """,
            repo,
            github_number,
        )
        if row is None:
            return None
        return dict(row)

    async def get_activity(
        self,
        issue_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Fetch audit log entries associated with an issue."""
        rows = await self._db.fetch(
            """
            SELECT *
            FROM audit_log
            WHERE issue_id = $1
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
            """,
            issue_id,
            limit,
            offset,
        )
        return [dict(r) for r in rows]
