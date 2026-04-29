"""Issues repository — PostgreSQL CRUD for issue tracking."""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from athanor.storage.database import DatabasePool
from athanor.storage.repositories.jobs import StatusTransitionConflict
from athanor.storage.schemas import IssueStatus

logger = structlog.get_logger(__name__)

_ALLOWED_UPDATE_FIELDS = frozenset(
    {
        "title",
        "body",
        "status",
        "priority",
        "labels",
        "repo",
        "parent_issue_id",
        "github_number",
        "github_url",
        "github_sync_enabled",
        "github_synced_at",
    }
)

_ALLOWED_SORT_COLUMNS = frozenset({"created_at", "updated_at", "priority"})

# Valid issue status transitions: current_status -> set of allowed next statuses
VALID_ISSUE_TRANSITIONS: dict[str, set[str]] = {
    IssueStatus.OPEN: {
        IssueStatus.TRIAGING,
        IssueStatus.IN_PROGRESS,
        IssueStatus.CLOSED,
        IssueStatus.CANCELLED,
    },
    IssueStatus.TRIAGING: {
        IssueStatus.OPEN,
        IssueStatus.IN_PROGRESS,
        IssueStatus.AWAITING_INPUT,
        IssueStatus.CLOSED,
        IssueStatus.CANCELLED,
    },
    IssueStatus.IN_PROGRESS: {
        IssueStatus.OPEN,
        IssueStatus.AWAITING_INPUT,
        IssueStatus.PAUSED,
        IssueStatus.CLOSED,
        IssueStatus.CANCELLED,
    },
    IssueStatus.AWAITING_INPUT: {
        IssueStatus.OPEN,
        IssueStatus.TRIAGING,
        IssueStatus.IN_PROGRESS,
        IssueStatus.CLOSED,
        IssueStatus.CANCELLED,
    },
    IssueStatus.PAUSED: {
        IssueStatus.OPEN,
        IssueStatus.TRIAGING,
        IssueStatus.IN_PROGRESS,
        IssueStatus.CLOSED,
        IssueStatus.CANCELLED,
    },
    IssueStatus.CLOSED: {
        IssueStatus.OPEN,  # Reopen
    },
    IssueStatus.CANCELLED: {
        IssueStatus.OPEN,  # Reopen
    },
}


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
            labels,
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
                (SELECT COUNT(*) FROM issues c WHERE c.parent_issue_id = i.id AND c.status = 'closed') AS children_closed_count,
                (SELECT COUNT(*) FROM jobs j WHERE j.issue_id = i.id) AS jobs_count,
                (SELECT t.agent_type FROM jobs j
                 JOIN traces t ON t.job_id = j.job_id
                 WHERE j.issue_id = i.id AND j.status = 'running'
                 ORDER BY t.created_at DESC LIMIT 1) AS active_agent
            FROM issues i
            WHERE i.id = $1
            """,
            issue_id,
        )
        if row is None:
            return None
        return dict(row)

    async def list_all(
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
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            args.append(f"%{escaped}%")
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
                (SELECT COUNT(*) FROM issues c WHERE c.parent_issue_id = i.id AND c.status = 'closed') AS children_closed_count,
                (SELECT COUNT(*) FROM jobs j WHERE j.issue_id = i.id) AS jobs_count,
                (SELECT t.agent_type FROM jobs j
                 JOIN traces t ON t.job_id = j.job_id
                 WHERE j.issue_id = i.id AND j.status = 'running'
                 ORDER BY t.created_at DESC LIMIT 1) AS active_agent
            FROM issues i
            {where}
            ORDER BY i.{sort_col} {sort_dir}
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *args,
        )
        return [dict(r) for r in rows], int(total or 0)

    async def update(self, issue_id: str, **fields: Any) -> None:
        """Update specific fields on an issue. updated_at is always refreshed.

        When a status transition is requested, uses a compare-and-swap UPDATE
        (WHERE status=$expected RETURNING id) to eliminate the read-then-write
        race. Raises StatusTransitionConflict if no row is updated (another
        writer changed the status concurrently).
        """
        valid = {k: v for k, v in fields.items() if k in _ALLOWED_UPDATE_FIELDS}
        if not valid:
            return

        new_status = valid.get("status")
        current_status: str | None = None

        if new_status is not None:
            # Pre-read current status for transition validation and CAS guard.
            row = await self._db.fetchrow("SELECT status FROM issues WHERE id = $1", issue_id)
            if row is None:
                # Issue does not exist — skip silently (existing behaviour).
                return
            current_status = row["status"]

            if current_status == new_status:
                # No-op: same status; still apply other fields if any.
                valid.pop("status")
                if not valid:
                    return
                # Fall through to update remaining fields without status CAS.
                new_status = None

            else:
                allowed = VALID_ISSUE_TRANSITIONS.get(current_status, set())
                if new_status not in allowed:
                    logger.warning(
                        "invalid_issue_status_transition",
                        issue_id=issue_id,
                        current=current_status,
                        requested=new_status,
                        allowed=sorted(allowed),
                    )
                    raise ValueError(
                        f"Invalid issue status transition: {current_status} -> {new_status}. "
                        f"Allowed: {sorted(allowed)}"
                    )

        # Build SET clause — updated_at is always refreshed.
        sets: list[str] = ["updated_at = NOW()"]
        args: list[Any] = []
        idx = 1
        for key, value in valid.items():
            sets.append(f"{key} = ${idx}")
            args.append(value)
            idx += 1

        if new_status is not None:
            # CAS: add WHERE status=$current_status to detect concurrent updates.
            args.append(issue_id)
            args.append(current_status)
            result_row = await self._db.fetchrow(
                f"UPDATE issues SET {', '.join(sets)} WHERE id = ${idx} AND status = ${idx + 1} RETURNING id",
                *args,
            )
            if result_row is None:
                raise StatusTransitionConflict(
                    f"issue {issue_id}: expected status={current_status!r}; "
                    "not found or status already changed by concurrent writer"
                )
        else:
            # No status change — plain UPDATE without CAS guard.
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
                (SELECT COUNT(*) FROM issues c WHERE c.parent_issue_id = i.id AND c.status = 'closed') AS children_closed_count,
                (SELECT COUNT(*) FROM jobs j WHERE j.issue_id = i.id) AS jobs_count,
                (SELECT t.agent_type FROM jobs j
                 JOIN traces t ON t.job_id = j.job_id
                 WHERE j.issue_id = i.id AND j.status = 'running'
                 ORDER BY t.created_at DESC LIMIT 1) AS active_agent
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
                (SELECT COUNT(*) FROM issues c WHERE c.parent_issue_id = i.id AND c.status = 'closed') AS children_closed_count,
                (SELECT COUNT(*) FROM jobs j WHERE j.issue_id = i.id) AS jobs_count,
                (SELECT t.agent_type FROM jobs j
                 JOIN traces t ON t.job_id = j.job_id
                 WHERE j.issue_id = i.id AND j.status = 'running'
                 ORDER BY t.created_at DESC LIMIT 1) AS active_agent
            FROM issues i
            WHERE i.repo = $1 AND i.github_number = $2
            """,
            repo,
            github_number,
        )
        if row is None:
            return None
        return dict(row)

    async def update_parent_status(self, issue_id: str) -> None:
        """Recalculate parent issue status based on children statuses."""
        issue = await self.get(issue_id)
        if not issue or not issue.get("parent_issue_id"):
            return

        parent_id = issue["parent_issue_id"]
        children = await self.get_children(parent_id)
        if not children:
            return

        # Filter out cancelled children
        active = [c for c in children if c["status"] != "cancelled"]
        if not active:
            return

        statuses = {c["status"] for c in active}

        if "triaging" in statuses:
            new_status = "triaging"
        elif "in_progress" in statuses:
            new_status = "in_progress"
        elif statuses == {"closed"}:
            new_status = "closed"
        else:
            new_status = "open"

        parent = await self.get(parent_id)
        if parent and parent["status"] != new_status:
            await self.update(parent_id, status=new_status)

    async def find_by_id_prefix(self, prefix: str) -> list[dict[str, Any]]:
        """Return issues whose id starts with ``prefix``.

        Used by the PR-linking webhook to match a branch name like
        ``athanor/<short-id>-<slug>`` to its parent issue. Issue IDs are
        unique 12-char hex prefixes so this returns 0 or 1 row in practice.

        Escapes backslashes, underscores and percent signs in ``prefix`` so a
        malicious branch name can't act as a LIKE wildcard.
        """
        if not prefix:
            return []
        # Escape LIKE special chars so user-supplied prefix can't act as a glob.
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = await self._db.fetch(
            "SELECT * FROM issues WHERE id LIKE $1 ESCAPE '\\' LIMIT 10",
            escaped + "%",
        )
        return [dict(r) for r in rows]

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
