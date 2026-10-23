"""Jobs repository — PostgreSQL CRUD for job lifecycle."""

from __future__ import annotations

import uuid
from collections.abc import Collection
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog

from embry0.safety.error_codes import ErrorCode
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.gate_refusal import gate_refusal_sql
from embry0.storage.schemas import JobStatus

if TYPE_CHECKING:
    from embry0.metrics import LoopMetrics

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
        JobStatus.SKIPPED,
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
    JobStatus.SKIPPED: set(),  # Terminal (#474)
    JobStatus.PR_MERGED: set(),  # Terminal
    # A closed PR can be reopened, and then merged (#279): merge sync walks
    # the status back to COMPLETED or on to PR_MERGED so it keeps saying what
    # the forge says. A merge is final.
    JobStatus.PR_CLOSED: {JobStatus.PR_MERGED, JobStatus.COMPLETED},
}

# #348: the one runtime path from PENDING straight to FAILED. The dispatch
# budget gate is asked before a job is marked running, so a job it refuses
# never ran and must not carry a started_at it did not earn. Narrow on
# purpose: only a write carrying ERR_BUDGET_REFUSED may take it, so every
# other failure still has to pass through RUNNING. Kept out of the table
# above because the table cannot express the condition.
_GATE_REFUSAL_CODE = ErrorCode.BUDGET_REFUSED.value


def _is_gate_refusal(current_status: str, new_status: str, fields: dict[str, Any]) -> bool:
    return (
        current_status == JobStatus.PENDING
        and new_status == JobStatus.FAILED
        and fields.get("error_code") == _GATE_REFUSAL_CODE
    )


# RAV-1271: statuses a live job terminates into. Entering any of these must
# always stamp finished_at (structural invariant — see update()). PR_MERGED /
# PR_CLOSED are post-terminal follow-ups of COMPLETED and excluded: their
# finished_at was set when the job completed and must not move.
_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        JobStatus.COMPLETED,
        JobStatus.PARTIAL,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.EXPIRED,
        JobStatus.SKIPPED,
    }
)

#: The statuses a live job ends in. Public so the services layer can ask
#: "is this row terminal?" with the repository's own answer (plan B).
TERMINAL_JOB_STATUSES: frozenset[str] = _TERMINAL_STATUSES

#: Statuses after which a job never runs again or writes its log: the
#: terminal ones and the pull request follow-ups of a completed job. An
#: allow-list, so a status added to JobStatus later reads as live and what
#: it holds is kept (the retention sweeps, ADR 0017).
RELEASED_JOB_STATUSES: frozenset[str] = frozenset(_TERMINAL_STATUSES | {JobStatus.PR_MERGED, JobStatus.PR_CLOSED})

# What a terminal UPDATE returns, for the outcome metrics (#285).
_FINISHED_COLUMNS = "job_id, status, pipeline_template, error_code, created_at, started_at, finished_at"

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
        "job_kind",
        "result_summary",
    }
)


def _text_or_none(value: Any) -> str | None:
    """A str as is, anything else None: the correlation columns are TEXT, and a
    non-string in the payload must not fail the INSERT and drop the event."""
    return value if isinstance(value, str) else None


class JobsRepository:
    """CRUD operations for the jobs table."""

    def __init__(self, db: DatabasePool, *, metrics: LoopMetrics | None = None) -> None:
        self._db = db
        # #285: every live job ends through update() or fail_orphaned(), so
        # outcomes are counted here, once. None = count nothing.
        self._metrics = metrics

    def _count_finished(self, row: Any) -> None:
        """One job reached a terminal status; ``row`` is the UPDATE's RETURNING."""
        if self._metrics is None:
            return
        try:
            fields = dict(row)
            started = fields.get("started_at") or fields.get("created_at")
            finished = fields.get("finished_at")
            duration = (finished - started).total_seconds() if started and finished else None
            self._metrics.job_finished(
                pipeline_template=fields.get("pipeline_template"),
                status=str(fields.get("status") or ""),
                error_code=fields.get("error_code"),
                duration_seconds=duration,
            )
        except Exception:
            logger.warning("job_finished_metric_failed", exc_info=True)

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
        dispatched_by: str | None = None,
    ) -> str:
        """Create a new job and return its ID.

        ``dispatched_by`` is the actor that asked for this job (a webhook
        sender login, an API principal). None when nobody identifiable did —
        the dispatch rate limit skips those rows rather than pooling them
        (#246).
        """
        if context is None:
            context = {"type": "git", "repo": repo} if repo else {"type": "none"}
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        await self._db.execute(
            """
            INSERT INTO jobs (
                job_id, repo, task, issue_number, issue_id,
                pipeline_template, pipeline_config, sandbox_profile, trace_id, context,
                dispatched_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
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
            dispatched_by,
        )
        logger.info("job_created", job_id=job_id, repo=repo, trace_id=trace_id)
        return job_id

    async def get(self, job_id: str) -> dict[str, Any] | None:
        """Fetch a single job by ID."""
        row = await self._db.fetchrow("SELECT * FROM jobs WHERE job_id = $1", job_id)
        if row is None:
            return None
        return dict(row)

    async def find_by_pr_url(self, pr_url: str, limit: int = 5) -> list[dict[str, Any]]:
        """Jobs that produced (or were linked to) a given PR, newest first.

        RAV-1092: comment-driven dispatch resolves an incoming PR comment to
        the job whose developer session created that PR, so the follow-up job
        can adopt the session.
        """
        rows = await self._db.fetch(
            "SELECT * FROM jobs WHERE pr_url = $1 ORDER BY created_at DESC LIMIT $2",
            pr_url,
            limit,
        )
        return [dict(r) for r in rows]

    async def list_all(
        self,
        status: str | None = None,
        repo: str | None = None,
        issue_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        pipeline_template: str | None = None,
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
        if pipeline_template:
            conditions.append(f"pipeline_template = ${idx}")
            args.append(pipeline_template)
            idx += 1

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        total = await self._db.fetchval(f"SELECT COUNT(*) FROM jobs {where}", *args)

        args.extend([limit, offset])
        rows = await self._db.fetch(
            f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ${idx} OFFSET ${idx + 1}",
            *args,
        )
        return [dict(r) for r in rows], int(total or 0)

    async def newest_per_repo(
        self, repos: Collection[str], *, pipeline_template: str, dispatched_by: str | None = None
    ) -> dict[str, dict[str, Any]]:
        """The newest job of each repository in ``repos`` that names ``pipeline_template``.

        A job ``dispatched_by`` started counts too, whatever its template
        column says now: plan_route rewrites the column with the template it
        resolved. Each row carries ``live``, whether any such job of the
        repository has not been released (``RELEASED_JOB_STATUSES``), not only
        the newest. One query for the whole set, without the task text; a
        repository with no such job is absent.

        A job the budget gate refused before any agent ran is left out
        (``gate_refusal_sql``, #331): it did no work, so it is nobody's
        newest attempt, and a repository whose only such jobs were refused
        is absent, as one with none.
        """
        if not repos:
            return {}
        rows = await self._db.fetch(
            f"""
            SELECT DISTINCT ON (repo)
                repo, job_id, status, created_at, finished_at, pipeline_config,
                bool_or(NOT (status = ANY($4::text[]))) OVER (PARTITION BY repo) AS live
            FROM jobs
            WHERE repo = ANY($1::text[]) AND (pipeline_template = $2 OR dispatched_by = $3)
              AND NOT {gate_refusal_sql("jobs")}
            ORDER BY repo, created_at DESC, job_id DESC
            """,
            sorted(set(repos)),
            pipeline_template,
            dispatched_by,
            sorted(str(status) for status in RELEASED_JOB_STATUSES),
        )
        return {str(r["repo"]): dict(r) for r in rows}

    async def count_dispatched_by(
        self,
        actor: str,
        *,
        since: datetime | None = None,
        tracker: str | None = None,
        tracker_key: str | None = None,
    ) -> int:
        """How many jobs ``actor`` dispatched — optionally since a moment, or for one ticket.

        The durable counter behind an automated dispatcher's own bounds (#284).
        A job counts whatever became of it — the bound is on attempts that did
        work, not on attempts that went well — with one exception: a job the
        budget gate refused before any agent ran did no work and must not burn
        an attempt. An in-run budget hard stop did work and counts. What makes
        a refusal is defined once, in ``gate_refusal_sql`` (#331), which the
        per-actor rate limit reads too.
        ``tracker`` + ``tracker_key`` narrow the count to one tracked ticket.
        """
        conditions = [
            "j.dispatched_by = $1",
            f"NOT {gate_refusal_sql('j')}",
        ]
        args: list[Any] = [actor]
        if since is not None:
            args.append(since)
            conditions.append(f"j.created_at >= ${len(args)}")
        join = ""
        if tracker is not None and tracker_key is not None:
            join = "JOIN issues i ON i.id = j.issue_id"
            args.extend([tracker, tracker_key])
            conditions.append(f"i.tracker = ${len(args) - 1} AND i.tracker_key = ${len(args)}")
        count = await self._db.fetchval(f"SELECT COUNT(*) FROM jobs j {join} WHERE {' AND '.join(conditions)}", *args)
        return int(count or 0)

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
                if new_status not in allowed and not _is_gate_refusal(current_status, new_status, valid):
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

        # #280: storing a PR URL is the first delivery fact — the job has an
        # open PR until merge sync learns otherwise. Only ever lifts `none`,
        # so a late pr_url write cannot undo a recorded merge or close.
        if valid.get("pr_url"):
            sets.append("delivery_state = CASE WHEN delivery_state = 'none' THEN 'pr_open' ELSE delivery_state END")

        # RAV-1271: structural invariant — a job can never enter a terminal
        # status without finished_at (an ask_user routing bug once produced
        # status=completed / finished_at=NULL rows that no dashboard bucket
        # or retention query could classify). Callers that forget the
        # timestamp get it stamped here; COALESCE keeps any existing value.
        if new_status is not None and new_status in _TERMINAL_STATUSES and "finished_at" not in valid:
            from datetime import UTC, datetime

            sets.append(f"finished_at = COALESCE(finished_at, ${idx})")
            args.append(datetime.now(UTC))
            idx += 1

        if not sets:
            return

        if new_status is not None:
            # CAS: add WHERE status=$current_status to detect concurrent updates.
            args.append(current_status)
            result_row = await self._db.fetchrow(
                f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = $1 AND status = ${idx} RETURNING {_FINISHED_COLUMNS}",
                *args,
            )
            if result_row is None:
                raise StatusTransitionConflict(
                    f"job {job_id}: expected status={current_status!r}; "
                    "not found or status already changed by concurrent writer"
                )
            if new_status in _TERMINAL_STATUSES:
                self._count_finished(result_row)
        else:
            # No status change — plain UPDATE without CAS guard.
            await self._db.execute(
                f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = $1",
                *args,
            )

    async def cas_transition_on_conn(
        self,
        conn: Any,
        job_id: str,
        new_status: str,
        expected_from: set[str],
    ) -> bool:
        """CAS status transition usable inside an external transaction (RAV-1063).

        ``update()`` always goes through the pool, so callers that must change
        status atomically with other writes (e.g. ``_handle_needs_info``'s
        input INSERTs) previously fell back to raw SQL — bypassing both the
        transition table and the CAS guard. This helper brings the lifecycle
        rules to their connection.

        Validates that ``new_status`` is a legal transition from every state
        in ``expected_from`` (ValueError otherwise — a programmer error, not
        a race). The UPDATE matches rows already in ``new_status`` too, so
        re-entry is an idempotent success. Returns False when no row matched:
        the job is missing or a concurrent writer moved it to a state outside
        ``expected_from`` — the caller decides whether that aborts its
        transaction.
        """
        for from_status in expected_from:
            allowed = VALID_JOB_TRANSITIONS.get(from_status, set())
            if new_status not in allowed:
                raise ValueError(
                    f"Invalid job status transition: {from_status} -> {new_status}. Allowed: {sorted(allowed)}"
                )
        # RAV-1271: same terminal-status invariant as update() — entering a
        # terminal status stamps finished_at (COALESCE keeps existing values).
        stamp = ", finished_at = COALESCE(finished_at, now())" if new_status in _TERMINAL_STATUSES else ""
        row = await conn.fetchrow(
            f"UPDATE jobs SET status = $2{stamp} "
            "WHERE job_id = $1 AND (status = ANY($3::text[]) OR status = $2) "
            "RETURNING job_id",
            job_id,
            new_status,
            sorted(expected_from),
        )
        if row is None:
            logger.warning(
                "job_cas_transition_unmatched",
                job_id=job_id,
                requested=new_status,
                expected_from=sorted(expected_from),
            )
            return False
        return True

    async def fail_orphaned(
        self,
        error_code: str,
        error_message: str,
        *,
        never_started_message: str,
        keep: Collection[str] = (),
        interrupted_code: str | None = None,
        interrupted_message: str | None = None,
    ) -> list[str]:
        """Boot-time reconciliation: fail every job left running/pending by a
        previous orchestrator lifecycle, except the ``keep`` ids boot is
        recovering (#307). Returns the affected job ids.

        A ``pending`` job never started: it may have waited for a run slot
        (MAX_RUNNING_JOBS, #583) for a long time. It gets
        ``never_started_message`` so its row does not claim it was running.
        A ``running`` job a drain stopped (``interrupted_at`` set) gets
        ``interrupted_code`` / ``interrupted_message`` when given, so a
        deploy-killed job reads as one.

        Deliberately set-based and outside per-row transition validation
        (pending -> failed is not a runtime transition): at startup nothing
        races, and the WHERE clause is its own from-state guard. Lives here so
        the lifecycle knowledge stays in the repository layer (RAV-1063).

        Being raw SQL, it carries the terminal-status invariant itself
        (RAV-1271): a failed row gets ``finished_at`` (#345). Without it an orphaned job
        had no duration, so the job-duration histogram never saw it.
        """
        rows = await self._db.fetch(
            """
            UPDATE jobs
            SET status = 'failed',
                error_message = CASE
                    WHEN status = 'pending' THEN $3
                    WHEN interrupted_at IS NOT NULL AND $5::text IS NOT NULL THEN $6
                    ELSE $1 END,
                error_code = CASE
                    WHEN status = 'running' AND interrupted_at IS NOT NULL AND $5::text IS NOT NULL THEN $5
                    ELSE $2 END,
                finished_at = COALESCE(finished_at, NOW())
            WHERE status IN ('running', 'pending')
              AND NOT (job_id = ANY($4::text[]))
            RETURNING job_id, status, pipeline_template, error_code, created_at, started_at, finished_at
            """,
            error_message,
            error_code,
            never_started_message,
            list(keep),
            interrupted_code,
            interrupted_message or error_message,
        )
        for row in rows:
            self._count_finished(row)
        return [r["job_id"] for r in rows]

    async def fail_jobs(self, job_ids: Collection[str], error_code: str, error_message: str) -> list[str]:
        """Boot-time: fail these in-flight jobs with one code and message.

        Set-based like :meth:`fail_orphaned`, for the jobs boot will not
        recover for a reason of its own (``ERR_RESUME_LIMIT``, #307).
        """
        if not job_ids:
            return []
        rows = await self._db.fetch(
            f"""
            UPDATE jobs
            SET status = 'failed', error_code = $2, error_message = $3,
                finished_at = COALESCE(finished_at, NOW())
            WHERE job_id = ANY($1::text[]) AND status IN ('running', 'pending')
            RETURNING {_FINISHED_COLUMNS}
            """,
            list(job_ids),
            error_code,
            error_message,
        )
        for row in rows:
            self._count_finished(row)
        return [r["job_id"] for r in rows]

    async def inflight_for_recovery(self) -> list[dict[str, Any]]:
        """The jobs a previous orchestrator left ``running`` or ``pending``,
        with what boot recovery decides on (#307)."""
        rows = await self._db.fetch(
            """
            SELECT job_id, status, pipeline_template, issue_id, launch, interrupted_at, resume_attempts
            FROM jobs
            WHERE status IN ('running', 'pending')
            ORDER BY created_at
            """
        )
        return [dict(r) for r in rows]

    async def begin_recovery(self, job_ids: Collection[str], *, counted: Collection[str]) -> list[str]:
        """Take these in-flight jobs back at boot (#307).

        Clears the drain stamp, so the next stop is judged on its own, and
        spends one ``resume_attempts`` on each ``counted`` job (its last stop
        was unclean). Returns the ids still in flight.
        """
        if not job_ids:
            return []
        rows = await self._db.fetch(
            """
            UPDATE jobs
            SET interrupted_at = NULL,
                resume_attempts = resume_attempts + CASE WHEN job_id = ANY($2::text[]) THEN 1 ELSE 0 END
            WHERE job_id = ANY($1::text[]) AND status IN ('running', 'pending')
            RETURNING job_id
            """,
            list(job_ids),
            list(counted),
        )
        return [r["job_id"] for r in rows]

    async def mark_interrupted(self, job_ids: Collection[str]) -> list[str]:
        """Stamp ``interrupted_at`` on the jobs a drain stopped or deferred
        (#307). Only jobs still in flight; returns their ids."""
        if not job_ids:
            return []
        rows = await self._db.fetch(
            """
            UPDATE jobs SET interrupted_at = NOW()
            WHERE job_id = ANY($1::text[]) AND status IN ('running', 'pending')
            RETURNING job_id
            """,
            list(job_ids),
        )
        return [r["job_id"] for r in rows]

    async def record_launch(self, job_id: str, launch: dict[str, Any]) -> None:
        """Store what starting this job again takes (#307): the runner kind
        and its arguments, JSON-shaped. Boot launches a ``pending`` job, or a
        ``running`` one whose graph never began, from it."""
        await self._db.execute("UPDATE jobs SET launch = $2 WHERE job_id = $1", job_id, launch)

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
            "INSERT INTO job_logs (job_id, stream, text, run_id, node, event_type) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
            job_id,
            "pipeline",
            json_mod.dumps(event, default=str),
            _text_or_none(event.get("run_id")),
            _text_or_none(event.get("node")),
            _text_or_none(event.get("type")),
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

    async def live_job_repos(self) -> set[str]:
        """The repos with a job not yet released (any status outside RELEASED_JOB_STATUSES)."""
        rows = await self._db.fetch(
            "SELECT DISTINCT repo FROM jobs WHERE status <> ALL($1::text[])",
            sorted(str(status) for status in RELEASED_JOB_STATUSES),
        )
        return {row["repo"] for row in rows}

    async def fetch_statuses(self, job_ids: list[str]) -> dict[str, str]:
        """Return ``{job_id: status}`` for the given ids that have a row.

        Ids with no ``jobs`` row are absent from the result — the volume
        reaper (#274) relies on that to tell "terminal" from "unknown".
        """
        if not job_ids:
            return {}
        rows = await self._db.fetch(
            "SELECT job_id, status FROM jobs WHERE job_id = ANY($1::text[])",
            job_ids,
        )
        return {row["job_id"]: row["status"] for row in rows}

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
