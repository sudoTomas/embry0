"""Jobs repository — PostgreSQL CRUD for job lifecycle."""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from embry0.storage.database import DatabasePool
from embry0.storage.schemas import JobStatus

logger = structlog.get_logger(__name__)


class StatusTransitionConflict(RuntimeError):
    """Raised when a CAS status update finds no matching row.

    Indicates that another writer has already changed the job's status since
    the caller last read it. Callers should treat this as a 409 Conflict.
    """


# Valid status transitions: current_status -> set of allowed next statuses
VALID_JOB_TRANSITIONS: dict[str, set[str]] = {
    JobStatus.PENDING: {JobStatus.RUNNING, JobStatus.CANCELLED},
    JobStatus.RUNNING: {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.PARTIAL,
        JobStatus.CANCELLED,
        JobStatus.AWAITING_INPUT,
        JobStatus.PAUSED,
    },
    JobStatus.AWAITING_INPUT: {
        JobStatus.RUNNING,
        JobStatus.CANCELLED,
        JobStatus.FAILED,
    },
    JobStatus.PAUSED: {
        JobStatus.RUNNING,
        JobStatus.CANCELLED,
        JobStatus.FAILED,
        JobStatus.EXPIRED,
    },
    JobStatus.COMPLETED: {
        JobStatus.PR_MERGED,
        JobStatus.PR_CLOSED,
    },
    JobStatus.PARTIAL: set(),  # Terminal
    JobStatus.FAILED: set(),  # Terminal
    JobStatus.CANCELLED: set(),  # Terminal
    JobStatus.EXPIRED: set(),  # Terminal
    JobStatus.PR_MERGED: set(),  # Terminal
    JobStatus.PR_CLOSED: set(),  # Terminal
}

_UPDATABLE_FIELDS = frozenset(
    {
        "status",
        "pipeline_template",
        "pipeline_config",
        "sandbox_profile",
        "total_cost_usd",
        "budget_overrun_usd",
        "pr_url",
        "error_message",
        "error_code",
        "started_at",
        "finished_at",
        "trace_id",
        "current_stage",
    }
)


class JobsRepository:
    """CRUD operations for the jobs table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def create(
        self,
        repo: str | None = None,
        task: str | None = None,
        issue_number: int | None = None,
        issue_id: str | None = None,
        pipeline_template: str | None = None,
        pipeline_config: dict[str, Any] | None = None,
        sandbox_profile: str | None = None,
        trace_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Create a new job and return its ID."""
        if context is None:
            context = {"type": "git", "repo": repo} if repo else {"type": "none"}
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        await self._db.execute(
            """
            INSERT INTO jobs (
                job_id, repo, task, issue_number, issue_id,
                pipeline_template, pipeline_config, sandbox_profile, trace_id, context
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            job_id,
            repo,
            task,
            issue_number,
            issue_id,
            pipeline_template,
            pipeline_config,
            sandbox_profile,
            trace_id,
            context,
        )
        logger.info("job_created", job_id=job_id, repo=repo, trace_id=trace_id)
        return job_id

    async def get(self, job_id: str) -> dict[str, Any] | None:
        """Fetch a single job by ID."""
        row = await self._db.fetchrow("SELECT * FROM jobs WHERE job_id = $1", job_id)
        if row is None:
            return None
        return dict(row)

    async def list_all(
        self,
        status: str | None = None,
        repo: str | None = None,
        issue_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """List jobs with optional filters. Returns (rows, total_count)."""
        conditions: list[str] = []
        args: list[Any] = []
        idx = 1

        if status:
            conditions.append(f"status = ${idx}")
            args.append(status)
            idx += 1
        if repo:
            conditions.append(f"repo = ${idx}")
            args.append(repo)
            idx += 1
        if issue_id:
            conditions.append(f"issue_id = ${idx}")
            args.append(issue_id)
            idx += 1

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        total = await self._db.fetchval(f"SELECT COUNT(*) FROM jobs {where}", *args)

        args.extend([limit, offset])
        rows = await self._db.fetch(
            f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ${idx} OFFSET ${idx + 1}",
            *args,
        )
        return [dict(r) for r in rows], int(total or 0)

    async def update(self, job_id: str, **fields: Any) -> None:
        """Update specific fields on a job. Validates status transitions.

        When a status transition is requested, uses a compare-and-swap UPDATE
        (WHERE status=$expected RETURNING job_id) to eliminate the read-then-write
        race. Raises StatusTransitionConflict if no row is updated (another
        writer changed the status concurrently).
        """
        valid = {k: v for k, v in fields.items() if k in _UPDATABLE_FIELDS}
        if not valid:
            return

        new_status = valid.get("status")
        current_status: str | None = None

        if new_status is not None:
            # Pre-read current status for transition validation and CAS guard.
            row = await self._db.fetchrow("SELECT status FROM jobs WHERE job_id = $1", job_id)
            if row is None:
                # Job does not exist — skip silently (existing behaviour).
                return
            current_status = row["status"]

            if current_status == new_status:
                # No-op: same status; still apply other fields if any.
                valid.pop("status")
                if not valid:
                    logger.debug("job_status_update_noop", job_id=job_id, status=new_status)
                    return
                # Fall through to update remaining fields without status CAS.
                new_status = None

            else:
                allowed = VALID_JOB_TRANSITIONS.get(current_status, set())
                if new_status not in allowed:
                    logger.warning(
                        "invalid_job_status_transition",
                        job_id=job_id,
                        current=current_status,
                        requested=new_status,
                        allowed=sorted(allowed),
                    )
                    raise ValueError(
                        f"Invalid job status transition: {current_status} -> {new_status}. Allowed: {sorted(allowed)}"
                    )

        # Build SET clause for all fields in valid.
        sets: list[str] = []
        args: list[Any] = [job_id]
        idx = 2
        for key, value in valid.items():
            sets.append(f"{key} = ${idx}")
            args.append(value)
            idx += 1

        if not sets:
            return

        if new_status is not None:
            # CAS: add WHERE status=$current_status to detect concurrent updates.
            args.append(current_status)
            result_row = await self._db.fetchrow(
                f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = $1 AND status = ${idx} RETURNING job_id",
                *args,
            )
            if result_row is None:
                raise StatusTransitionConflict(
                    f"job {job_id}: expected status={current_status!r}; "
                    "not found or status already changed by concurrent writer"
                )
        else:
            # No status change — plain UPDATE without CAS guard.
            await self._db.execute(
                f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = $1",
                *args,
            )

    async def increment_cost(self, job_id: str, cost_delta: float) -> None:
        """Atomically add cost_delta to total_cost_usd."""
        await self._db.execute(
            "UPDATE jobs SET total_cost_usd = total_cost_usd + $2 WHERE job_id = $1",
            job_id,
            cost_delta,
        )

    async def append_log_event(self, job_id: str, event: dict[str, Any]) -> int:
        """Append a pipeline event to the job_logs table.

        Returns the inserted row's ``id`` — a monotonic BIGSERIAL used as the
        event sequence number for WS replay cursoring (see Plan B1).
        """
        import json as json_mod

        row = await self._db.fetchrow(
            "INSERT INTO job_logs (job_id, stream, text) VALUES ($1, $2, $3) RETURNING id",
            job_id,
            "pipeline",
            json_mod.dumps(event, default=str),
        )
        assert row is not None, "INSERT ... RETURNING must return a row"
        return int(row["id"])

    async def fetch_active_sandbox_containers(self) -> set[str]:
        """Return the set of `sandbox-<job_id>` container names that back
        currently-active jobs (any non-terminal status).

        Used by ContainerReaper to avoid destroying live work.
        """
        rows = await self._db.fetch(
            "SELECT job_id FROM jobs WHERE status IN ('pending', 'running', 'awaiting_input', 'paused')"
        )
        return {f"sandbox-{row['job_id']}" for row in rows}

    async def queue_summary(self) -> dict[str, int]:
        """Return per-status counts for currently-active jobs.

        Used by GET /api/v1/queue. Statuses returned:
        - pending: jobs not yet started
        - running: jobs currently executing
        - awaiting_input: jobs paused on user questions
        - paused: jobs paused by the reaper or operator
        """
        rows = await self._db.fetch(
            "SELECT status, COUNT(*) AS n FROM jobs "
            "WHERE status IN ('pending', 'running', 'awaiting_input', 'paused') "
            "GROUP BY status"
        )
        result = {"pending": 0, "running": 0, "awaiting_input": 0, "paused": 0}
        for row in rows:
            result[row["status"]] = int(row["n"])
        return result

    async def get_log_events(
        self,
        job_id: str,
        stream: str = "pipeline",
        limit: int = 500,
        since_seq: int = 0,
    ) -> list[dict[str, Any]]:
        """Get pipeline events for a job, ordered chronologically by seq id.

        ``since_seq=N`` filters to rows with ``id > N`` — used as the WS replay
        cursor so reconnecting clients only get events they haven't seen.
        Ordering is by ``id`` (monotonic) rather than ``created_at`` (can tie).
        """
        import json as json_mod

        rows = await self._db.fetch(
            """
            SELECT * FROM job_logs
            WHERE job_id = $1 AND stream = $2 AND id > $3
            ORDER BY id ASC
            LIMIT $4
            """,
            job_id,
            stream,
            since_seq,
            limit,
        )
        result = []
        for r in rows:
            row_dict = dict(r)
            try:
                row_dict["payload"] = json_mod.loads(row_dict.get("text", "{}"))
            except (json_mod.JSONDecodeError, TypeError):
                row_dict["payload"] = {"type": "raw", "message": row_dict.get("text", "")}
            result.append(row_dict)
        return result
