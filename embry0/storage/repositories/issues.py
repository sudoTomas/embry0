"""Issues repository — PostgreSQL CRUD for issue tracking."""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from typing import Any

import structlog

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.jobs import StatusTransitionConflict, job_repository_sql
from embry0.storage.schemas import IssueStatus

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
        # GitHub outbound-push feature flags — not tracker refs (#161).
        "github_sync_enabled",
        "github_synced_at",
        # Generic tracker reference (#165, #161): the ONLY ref columns.
        "tracker",
        "tracker_key",
        "tracker_url",
        "tracker_native_id",
        # Per-issue ask-user fan-out channels (jsonb list of strings).
        "notification_channels",
    }
)

_ALLOWED_SORT_COLUMNS = frozenset({"created_at", "updated_at", "priority"})


def _within(column: str, repos: str | None) -> str:
    """`` AND`` ``column`` names one of the repositories in the ``text[]`` placeholder ``repos`` (lower-cased slugs;
    NULL keeps every repository), or nothing when there is no placeholder."""
    if repos is None:
        return ""
    return f" AND ({repos}::text[] IS NULL OR lower({column}) = ANY({repos}::text[]))"


def _issue_columns(repos: str | None = None) -> str:
    """What an issue row ``i`` is read with: the row, how many children it has (and how many are closed), how many
    jobs, and the agent of its newest running job. ``repos``, a ``text[]`` placeholder of lower-cased slugs, counts
    only the children and the jobs of those repositories (a job's by :func:`job_repository_sql`)."""
    child, job = _within("c.repo", repos), _within(job_repository_sql("j"), repos)
    return f"""
                i.*,
                (SELECT COUNT(*) FROM issues c WHERE c.parent_issue_id = i.id{child}) AS children_count,
                (SELECT COUNT(*) FROM issues c
                 WHERE c.parent_issue_id = i.id AND c.status = 'closed'{child}) AS children_closed_count,
                (SELECT COUNT(*) FROM jobs j WHERE j.issue_id = i.id{job}) AS jobs_count,
                (SELECT t.agent_type FROM jobs j
                 JOIN traces t ON t.job_id = j.job_id
                 WHERE j.issue_id = i.id AND j.status = 'running'{job}
                 ORDER BY t.created_at DESC, t.trace_id DESC LIMIT 1) AS active_agent"""


def _repos_arg(repos: Sequence[str] | None) -> list[str] | None:
    return None if repos is None else list(repos)


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
        notification_channels: list[str] | None = None,
        tracker: str | None = None,
        tracker_key: str | None = None,
        tracker_url: str | None = None,
        tracker_native_id: str | None = None,
    ) -> str:
        """Create a new issue and return its ID.

        ``notification_channels``: per-issue jsonb list of channels to fan
        agent ask-user questions out to (Plan A Task 2). When ``None`` the
        column's DB default (``["dashboard"]``) is used; when provided the
        list is inserted verbatim. Caller is responsible for validating the
        contents (the API layer does this via the AskUserChannel enum).

        The ``tracker*`` reference rides the same INSERT (#284). It used to be
        set by a follow-up UPDATE, so a second writer racing on the same
        ticket lost on ``idx_issues_tracker_ref`` only *after* its row
        existed, leaving an orphan with no tracker ref. In one statement the
        loser's row is never created; it raises ``asyncpg.UniqueViolationError``.
        """
        issue_id = f"iss-{uuid.uuid4().hex[:12]}"
        columns: dict[str, Any] = {
            "id": issue_id,
            "title": title,
            "body": body,
            "priority": priority,
            "labels": labels if labels is not None else [],
            "repo": repo,
            "parent_issue_id": parent_issue_id,
            "github_sync_enabled": github_sync_enabled,
            "created_by": created_by,
        }
        if notification_channels is not None:
            # Otherwise rely on the column DEFAULT '["dashboard"]'::jsonb.
            columns["notification_channels"] = notification_channels
        if tracker is not None:
            columns.update(
                tracker=tracker,
                tracker_key=tracker_key,
                tracker_url=tracker_url,
                tracker_native_id=tracker_native_id,
            )
        placeholders = ", ".join(f"${n}" for n in range(1, len(columns) + 1))
        await self._db.execute(
            f"INSERT INTO issues ({', '.join(columns)}) VALUES ({placeholders})",
            *columns.values(),
        )
        logger.info("issue_created", issue_id=issue_id, title=title)
        return issue_id

    async def get(self, issue_id: str | None, *, repos: Sequence[str] | None = None) -> dict[str, Any] | None:
        """Fetch a single issue by ID, including children_count, jobs_count, and active_agent.

        ``issue_id=None`` (issue-less API-submitted jobs) returns None. ``repos`` (lower-cased slugs) answers None
        for an issue of any other repository, or of none, and counts only those repositories' children and jobs;
        None reads every issue and counts everything.
        """
        if issue_id is None:
            return None
        row = await self._db.fetchrow(
            f"""
            SELECT {_issue_columns("$2")}
            FROM issues i
            WHERE i.id = $1 AND ($2::text[] IS NULL OR lower(i.repo) = ANY($2::text[]))
            """,
            issue_id,
            _repos_arg(repos),
        )
        if row is None:
            return None
        return dict(row)

    async def repositories_of(self, issue_ids: Collection[str]) -> dict[str, str | None]:
        """The repository of each issue in ``issue_ids`` that exists, None for one with no repository. An issue that
        does not exist is absent."""
        if not issue_ids:
            return {}
        rows = await self._db.fetch("SELECT id, repo FROM issues WHERE id = ANY($1::text[])", sorted(set(issue_ids)))
        return {str(r["id"]): (str(r["repo"]) if r["repo"] else None) for r in rows}

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
        repos: Sequence[str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List issues with optional filters. Returns (rows, total_count).

        ``repos`` (lower-cased slugs) keeps only the issues of those repositories, before the page is cut and the
        total counted, and counts only their children and jobs; None keeps every issue.
        """
        conditions: list[str] = []
        args: list[Any] = []
        idx = 1
        within: str | None = None

        if repos is not None:
            within = f"${idx}"
            conditions.append(f"lower(i.repo) = ANY({within}::text[])")
            args.append(list(repos))
            idx += 1
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
            SELECT {_issue_columns(within)}
            FROM issues i
            {where}
            ORDER BY i.{sort_col} {sort_dir}, i.id {sort_dir}
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *args,
        )
        return [dict(r) for r in rows], int(total or 0)

    async def claim_answered(self, issue_id: str) -> bool:
        """Take an issue whose questions are answered: ``awaiting_input`` to ``triaging`` in one statement (#499).

        The one gate for going on with an issue once its last blocking question
        is answered, whether that resumes its waiting job or starts a fresh run:
        of two answers (or an answer and an inbound reply) that each find no
        question left, only the one that takes the issue goes on.
        """
        row = await self._db.fetchrow(
            "UPDATE issues SET status = 'triaging' WHERE id = $1 AND status = 'awaiting_input' RETURNING id",
            issue_id,
        )
        return row is not None

    async def update(self, issue_id: str | None, **fields: Any) -> None:
        """Update specific fields on an issue. updated_at is always refreshed.

        ``issue_id=None`` (issue-less API-submitted jobs) is a silent no-op,
        matching the missing-row behaviour below.

        When a status transition is requested, uses a compare-and-swap UPDATE
        (WHERE status=$expected RETURNING id) to eliminate the read-then-write
        race. Raises StatusTransitionConflict if no row is updated (another
        writer changed the status concurrently).
        """
        if issue_id is None:
            return
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
                        f"Invalid issue status transition: {current_status} -> {new_status}. Allowed: {sorted(allowed)}"
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

    async def cas_transition_on_conn(
        self,
        conn: Any,
        issue_id: str,
        new_status: str,
        expected_from: set[str],
    ) -> bool:
        """CAS status transition usable inside an external transaction (RAV-1063).

        Mirror of ``JobsRepository.cas_transition_on_conn`` — see its docstring.
        Validates legality against VALID_ISSUE_TRANSITIONS (ValueError), treats
        already-in-``new_status`` as idempotent success, returns False when no
        row matched (missing issue or concurrent writer).
        """
        for from_status in expected_from:
            allowed = VALID_ISSUE_TRANSITIONS.get(from_status, set())
            if new_status not in allowed:
                raise ValueError(
                    f"Invalid issue status transition: {from_status} -> {new_status}. Allowed: {sorted(allowed)}"
                )
        row = await conn.fetchrow(
            "UPDATE issues SET status = $2, updated_at = NOW() "
            "WHERE id = $1 AND (status = ANY($3::text[]) OR status = $2) "
            "RETURNING id",
            issue_id,
            new_status,
            sorted(expected_from),
        )
        if row is None:
            logger.warning(
                "issue_cas_transition_unmatched",
                issue_id=issue_id,
                requested=new_status,
                expected_from=sorted(expected_from),
            )
            return False
        return True

    async def reopen_stuck_for_jobs(self, job_ids: list[str]) -> int:
        """Boot-time reconciliation: reopen issues left triaging/awaiting_input
        by orphaned jobs. Returns the number of issues reopened.

        Set-based companion to ``JobsRepository.fail_orphaned`` — the WHERE
        clause is the from-state guard; nothing races at startup (RAV-1063).
        """
        rows = await self._db.fetch(
            """
            UPDATE issues SET status = 'open', updated_at = NOW()
            WHERE status IN ('triaging', 'awaiting_input')
            AND id IN (
                SELECT issue_id FROM jobs
                WHERE job_id = ANY($1::text[]) AND issue_id IS NOT NULL
            )
            RETURNING id
            """,
            job_ids,
        )
        return len(rows)

    async def stale_triaging(self, tracker: str, older_than_minutes: int, *, reopen: bool) -> list[dict[str, Any]]:
        """Rows of ``tracker`` stuck in ``triaging`` with no live job (#284).

        A dispatch moves the issue to ``triaging`` and *then* inserts the job.
        A process that dies between the two leaves a row no trigger will ever
        touch again — only ``open`` dispatches — and ``reopen_stuck_for_jobs``
        cannot see it, because there is no orphaned job to find it by. A row
        counts as stale when it has been ``triaging`` for ``older_than_minutes``
        (``updated_at`` is refreshed by the move itself, so a dispatch in
        flight is never stale) and none of its jobs is live. With ``reopen``
        the rows are returned to ``open`` in the same statement, the WHERE
        clause being the from-state guard; without it they are only reported.
        Returns ``[{id, tracker_key}]``.
        """
        stale = """
            i.status = 'triaging' AND i.tracker = $1
            AND i.updated_at < NOW() - make_interval(mins => $2)
            AND NOT EXISTS (
                SELECT 1 FROM jobs j
                WHERE j.issue_id = i.id AND j.status IN ('pending', 'running', 'awaiting_input', 'paused')
            )
        """
        if reopen:
            rows = await self._db.fetch(
                f"UPDATE issues i SET status = 'open', updated_at = NOW() WHERE {stale} RETURNING i.id, i.tracker_key",
                tracker,
                older_than_minutes,
            )
        else:
            rows = await self._db.fetch(
                f"SELECT i.id, i.tracker_key FROM issues i WHERE {stale}", tracker, older_than_minutes
            )
        return [dict(r) for r in rows]

    async def get_children(self, parent_issue_id: str, *, repos: Sequence[str] | None = None) -> list[dict[str, Any]]:
        """Fetch all direct children of an issue. ``repos`` (lower-cased slugs) keeps only the children of those
        repositories, and counts only their children and jobs; None keeps every child."""
        rows = await self._db.fetch(
            f"""
            SELECT {_issue_columns("$2")}
            FROM issues i
            WHERE i.parent_issue_id = $1 AND ($2::text[] IS NULL OR lower(i.repo) = ANY($2::text[]))
            ORDER BY i.created_at ASC, i.id ASC
            """,
            parent_issue_id,
            _repos_arg(repos),
        )
        return [dict(r) for r in rows]

    async def get_by_tracker_ref(self, tracker: str, repo: str | None, tracker_key: str) -> dict[str, Any] | None:
        """Fetch an issue by its generic tracker reference (#165, #161).

        The one dedupe lookup for every tracker — it replaced ``get_by_github``,
        ``get_by_linear`` and ``find_by_repo_and_github_number``. Scoped by repo
        because a forge-native id (a GitHub number, a GitLab iid) is only
        unique within its project; pass ``repo=None`` for trackers whose keys
        are globally unique (Linear's ``ENG-123`` — its dedupe runs *before*
        repo resolution, so there is no repo to scope by yet).

        Rows are enriched with the same children/jobs/active-agent counts as
        :meth:`get`, so webhook handlers can act on them without a re-fetch.
        """
        conditions = ["i.tracker = $1", "i.tracker_key = $2"]
        params: list[Any] = [tracker, tracker_key]
        if repo is not None:
            conditions.append("i.repo = $3")
            params.append(repo)
        row = await self._db.fetchrow(
            f"""
            SELECT {_issue_columns()}
            FROM issues i
            WHERE {" AND ".join(conditions)}
            """,
            *params,
        )
        return dict(row) if row is not None else None

    async def update_parent_status(self, issue_id: str | None) -> None:
        """Recalculate parent issue status based on children statuses.

        ``issue_id=None`` is a no-op (self.get returns None).
        """
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
        ``embry0/<short-id>-<slug>`` to its parent issue. Issue IDs are
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
            ORDER BY created_at DESC, id DESC
            LIMIT $2 OFFSET $3
            """,
            issue_id,
            limit,
            offset,
        )
        return [dict(r) for r in rows]
