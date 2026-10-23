"""Healing-cases repository — SQL for the lifecycle ledger (#278).

Storage only: which writes are legal is decided by
``embry0.services.healing``. Writers should go through
``HealingCaseService``, not this class.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import asyncpg
import structlog

from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

# Columns LockedCase.update may set. Names are interpolated into SQL, so this
# is an allowlist, not documentation.
_UPDATABLE = frozenset(
    {
        "stage",
        "resolution_path",
        "tracker",
        "tracker_key",
        "tracker_url",
        "issue_id",
        "merge_sha",
        "deploy_sha",
        "deploy_marker_at",
        "deploy_services",
        "healed_verdict",
        "verdict_evidence",
        "verdict_checked_at",
        "occurrence_count",
        "last_seen_at",
        "note",
        "tracked_at",
        "accepted_at",
        "pr_open_at",
        "merged_at",
        "deployed_at",
        "verified_at",
        "closed_at",
    }
)
_PR_UPDATABLE = frozenset(
    {"number", "head_sha", "head_sha_at", "state", "merge_sha", "job_id", "merged_at", "closed_at"}
)

# Stages in which a case still awaits acceptance — lifecycle.OPEN_UNACCEPTED_STAGES,
# restated here because storage does not import services; a test holds them equal.
CAP_STAGES: tuple[str, ...] = ("detected", "tracked")
# Every stage but `closed`, each with a `<stage>_at` column — lifecycle.Stage
# restated for the same reason; a test holds them equal.
OPEN_STAGES: tuple[str, ...] = ("detected", "tracked", "accepted", "pr_open", "merged", "deployed", "verified")

# THE proposal-cap predicate (#283 must use it, via the two methods below).
# A case holds one of the watcher's draft slots only while ALL of this holds:
#   * the watcher opened it — a human's or a webhook's ticket stuck at
#     `tracked`, or an alert/ci finding, must never eat a watcher slot;
#   * it has a tracker ticket — no ticket, no draft awaiting review;
#   * nobody accepted or closed it yet (CAP_STAGES);
#   * reconcile has not released its draft (cancelled/done/missing, or the
#     human accepted it — #415);
#   * it is inside the caller's age window (RAV-1073: a stale draft must age
#     out of the cap). Reconcile does not use this window. A case outlives it,
#     and a ticket cancelled afterwards must still close (#415).
_CAP_PREDICATE = """
    c.origin = 'watcher'
    AND c.tracker_key IS NOT NULL
    AND c.stage = ANY($1::text[])
    AND c.detected_at > NOW() - make_interval(days => $2)
    AND ($3::text IS NULL OR c.repo = $3)
    AND NOT EXISTS (
        SELECT 1 FROM watcher_runs w
        WHERE w.id = c.watcher_run_id AND w.released_at IS NOT NULL
    )
"""

# How many times a case was sent back for another fix attempt (#451).
_REOPENS = "(SELECT COUNT(*) FROM healing_case_events e WHERE e.case_id = c.id AND e.event = 'reopened')"

# A watcher finding a human has not answered yet: _CAP_PREDICATE without its
# age window and repo, with the stages inlined so it composes with list_page's
# own numbered filters, plus one kind of case the cap does not count. The
# window is dropped for the reason reconcile drops it — a draft that aged out
# of the cap is still waiting for its answer. A test holds this and the cap
# count to the same drafts inside the window.
#
# The extra kind: a case a `not_healed` verdict sent back to `tracked` (#658)
# waits for a person to accept the retry or close it, but holds no draft slot.
# Its draft was answered once — that acceptance released the watcher run — and
# the watcher filed nothing new, so it is a question for a person without
# being a draft that limits the watcher's filing. Only a reopen leaves a case
# at `tracked` after an acceptance, so "reopened and at a cap stage" is exactly
# "sent back and not accepted again".
#
# ADR 0011 adds one more kind: a need of an organization (origin `org`) is a
# question for a person for as long as it is open. It holds no draft slot
# and has no tracker ticket; dismissing or answering the need closes it.
_AWAITING_DECISION = f"""(
    (
        c.origin = 'watcher'
        AND c.tracker_key IS NOT NULL
        AND c.stage IN ({", ".join(f"'{stage}'" for stage in CAP_STAGES)})
        AND (
            NOT EXISTS (
                SELECT 1 FROM watcher_runs w
                WHERE w.id = c.watcher_run_id AND w.released_at IS NOT NULL
            )
            OR {_REOPENS} > 0
        )
    )
    OR (c.origin = 'org' AND c.stage = 'detected')
)"""

# lifecycle.fix_deployed, in SQL. NULL-safe, so NOT (...) is its exact negation;
# an API test holds the filter and the response field to the same answer.
_FIX_DEPLOYED = (
    "(c.merge_sha IS NOT NULL AND c.merged_at IS NOT NULL AND c.deployed_at IS NOT NULL"
    " AND c.deployed_at >= c.merged_at)"
)

# The PR that stands for a case in a one-line view: the merged one, else the
# newest open one, else the newest.
_CURRENT_PR = """
    LEFT JOIN LATERAL (
        SELECT p.url AS pr_url, p.number AS pr_number, p.head_sha AS pr_head_sha, p.state AS pr_state
        FROM healing_case_prs p WHERE p.case_id = c.id
        ORDER BY (p.state = 'merged') DESC, (p.state = 'open') DESC, p.id DESC LIMIT 1
    ) pr ON TRUE
"""


def _assignments(fields: dict[str, Any], allowed: frozenset[str], table: str) -> tuple[str, list[Any]]:
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"not updatable {table} columns: {sorted(unknown)}")
    names = list(fields)
    return ", ".join(f"{name} = ${i}" for i, name in enumerate(names, start=2)), [fields[n] for n in names]


class LockedCase:
    """One case row held ``FOR UPDATE`` inside an open transaction.

    Everything done through this object commits or rolls back together, so a
    stage change, its data, its child rows and its history row can never
    disagree. Lock order is always the case row first, children after.
    """

    def __init__(self, conn: asyncpg.Connection, row: dict[str, Any]) -> None:
        self._conn = conn
        self.row = row

    @property
    def case_id(self) -> str:
        return str(self.row["id"])

    async def release_draft_slot(self, at: datetime) -> int:
        """Stamp ``released_at`` on the watcher run that holds this draft's slot.

        Acceptance is the human's answer, so the slot ends in the same
        transaction as the case leaving ``tracked`` (#415). Already-released
        rows are left alone. A case with no run and no ticket matches nothing.
        """
        run_id = self.row.get("watcher_run_id")
        key = self.row.get("tracker_key")
        if run_id is None and not key:
            return 0
        result = await self._conn.execute(
            """
            UPDATE watcher_runs
               SET released_at = $1
             WHERE released_at IS NULL
               AND (
                    ($2::text IS NOT NULL AND id = $2)
                 OR (
                      $3::text IS NOT NULL
                      AND tracker_key = $3
                      AND ($4::text IS NULL OR tracker = $4)
                      AND (repo = '' OR $5::text IS NULL OR repo = $5)
                    )
               )
            """,
            at,
            run_id,
            key,
            self.row.get("tracker"),
            self.row.get("repo"),
        )
        released = int(str(result).split()[-1]) if result else 0
        if released:
            logger.info(
                "watcher_draft_slot_released",
                case_id=self.case_id,
                tracker_key=key,
                rows=released,
                reason="accepted",
            )
        return released

    async def update(self, fields: dict[str, Any]) -> None:
        if not fields:
            return
        assignments, values = _assignments(fields, _UPDATABLE, "healing_cases")
        updated = await self._conn.fetchrow(
            f"UPDATE healing_cases SET {assignments}, updated_at = NOW() WHERE id = $1 RETURNING *",
            self.case_id,
            *values,
        )
        self.row = dict(updated)

    # -- jobs

    async def link_job(self, job_id: str, attached_at: datetime) -> bool:
        """Link a job. Returns False when it was already linked."""
        status = await self._conn.execute(
            """
            INSERT INTO healing_case_jobs (case_id, job_id, attached_at)
            VALUES ($1, $2, $3) ON CONFLICT DO NOTHING
            """,
            self.case_id,
            job_id,
            attached_at,
        )
        return str(status).endswith(" 1")

    async def job_issue_id(self, job_id: str) -> tuple[bool, str | None]:
        """``(job exists, its issue_id)``."""
        row = await self._conn.fetchrow("SELECT issue_id FROM jobs WHERE job_id = $1", job_id)
        return (row is not None, row["issue_id"] if row else None)

    # -- pull requests

    async def get_pr(self, url: str) -> dict[str, Any] | None:
        """The PR row for ``url`` — whichever case owns it (url is unique)."""
        row = await self._conn.fetchrow("SELECT * FROM healing_case_prs WHERE url = $1 FOR UPDATE", url)
        return dict(row) if row else None

    async def insert_pr(
        self,
        *,
        url: str,
        number: int | None,
        head_sha: str | None,
        state: str,
        merge_sha: str | None,
        job_id: str | None,
        at: datetime,
    ) -> dict[str, Any]:
        row = await self._conn.fetchrow(
            """
            INSERT INTO healing_case_prs (
                case_id, url, number, head_sha, head_sha_at, state, merge_sha, job_id,
                opened_at, merged_at, closed_at
            )
            VALUES ($1, $2, $3, $4::text, CASE WHEN $4::text IS NULL THEN NULL ELSE $8::timestamptz END,
                    $5, $6, $7, $8,
                    CASE WHEN $5 = 'merged' THEN $8::timestamptz END,
                    CASE WHEN $5 = 'closed' THEN $8::timestamptz END)
            RETURNING *
            """,
            self.case_id,
            url,
            number,
            head_sha,
            state,
            merge_sha,
            job_id,
            at,
        )
        return dict(row)

    async def update_pr(self, pr_id: int, fields: dict[str, Any]) -> None:
        if not fields:
            return
        assignments, values = _assignments(fields, _PR_UPDATABLE, "healing_case_prs")
        await self._conn.execute(
            f"UPDATE healing_case_prs SET {assignments}, updated_at = NOW() WHERE id = $1", pr_id, *values
        )

    async def has_open_pr_besides(self, url: str) -> bool:
        return bool(
            await self._conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM healing_case_prs WHERE case_id = $1 AND state = 'open' AND url <> $2)",
                self.case_id,
                url,
            )
        )

    # -- history

    async def event_by_key(self, event_key: str) -> dict[str, Any] | None:
        row = await self._conn.fetchrow(
            "SELECT event, detail FROM healing_case_events WHERE case_id = $1 AND event_key = $2",
            self.case_id,
            event_key,
        )
        return dict(row) if row else None

    async def reported_deploys(self) -> list[dict[str, Any]]:
        """Deploys reported for the CURRENT fix attempt (``detail.deploy``),
        whether or not one became the anchor. Those from before the last
        ``reopened`` were vouched for a merge that has since failed."""
        rows = await self._conn.fetch(
            """
            SELECT detail -> 'deploy' AS deploy FROM healing_case_events
            WHERE case_id = $1 AND event = 'deployed' AND detail ? 'deploy'
              AND id > COALESCE((SELECT MAX(id) FROM healing_case_events
                                 WHERE case_id = $1 AND event = 'reopened'), 0)
            ORDER BY id
            """,
            self.case_id,
        )
        return [r["deploy"] for r in rows]

    async def last_reopen(self) -> dict[str, Any] | None:
        """``{id, occurred_at}`` of the case's latest ``reopened`` event."""
        row = await self._conn.fetchrow(
            """
            SELECT id, occurred_at FROM healing_case_events
            WHERE case_id = $1 AND event = 'reopened' ORDER BY id DESC LIMIT 1
            """,
            self.case_id,
        )
        return dict(row) if row else None

    async def merge_shas_before(self, event_id: int) -> set[str]:
        """Shas of merges reported before ``event_id`` — the discarded attempts'."""
        rows = await self._conn.fetch(
            """
            SELECT COALESCE(detail -> 'updates' ->> 'merge_sha', detail -> 'not_applied' ->> 'merge_sha') AS sha
            FROM healing_case_events
            WHERE case_id = $1 AND event = 'merged' AND id < $2
            """,
            self.case_id,
            event_id,
        )
        return {r["sha"] for r in rows if r["sha"]}

    async def post_close_recurrence(self) -> dict[str, Any] | None:
        """The one history row a closed case's recurrences are folded into."""
        row = await self._conn.fetchrow(
            """
            SELECT id, detail FROM healing_case_events
            WHERE case_id = $1 AND event = 'recurrence' AND detail ->> 'effect' = 'post_close'
            ORDER BY id DESC LIMIT 1 FOR UPDATE
            """,
            self.case_id,
        )
        return dict(row) if row else None

    async def replace_event_detail(self, event_id: int, detail: dict[str, Any]) -> None:
        await self._conn.execute(
            "UPDATE healing_case_events SET detail = $3 WHERE id = $1 AND case_id = $2", event_id, self.case_id, detail
        )

    async def event_recorded(self, event: str, detail: dict[str, Any]) -> bool:
        """Whether an identical history row exists (dedupe for keyless redeliveries)."""
        return bool(
            await self._conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM healing_case_events WHERE case_id = $1 AND event = $2 AND detail = $3)",
                self.case_id,
                event,
                detail,
            )
        )

    async def add_event(
        self,
        event: str,
        from_stage: str | None,
        to_stage: str,
        detail: dict[str, Any],
        occurred_at: datetime,
        event_key: str | None = None,
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO healing_case_events (case_id, event, event_key, from_stage, to_stage, detail, occurred_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            self.case_id,
            event,
            event_key,
            from_stage,
            to_stage,
            detail,
            occurred_at,
        )


class _BoundConnection:
    """The slice of ``DatabasePool`` this repository uses, served by ONE
    connection that is already inside a transaction: every statement joins
    it, and a nested ``transaction()`` becomes a savepoint."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        async with self._conn.transaction():
            yield self._conn

    async def execute(self, query: str, *args: Any) -> str:
        return str(await self._conn.execute(query, *args))

    async def fetchval(self, query: str, *args: Any) -> Any:
        return await self._conn.fetchval(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        return await self._conn.fetchrow(query, *args)

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        return list(await self._conn.fetch(query, *args))


class HealingCasesRepository:
    """Reads and transactional writes for healing_cases and its child tables."""

    def __init__(self, db: DatabasePool | _BoundConnection) -> None:
        self._db = db

    @asynccontextmanager
    async def atomic(self) -> AsyncIterator[HealingCasesRepository]:
        """A repository whose every call shares one transaction.

        A service built on it turns a multi-call sequence (open a case, link
        its jobs and PRs, close it) into all-or-nothing: an exception or a
        cancellation rolls the whole sequence back.
        """
        async with self._db.transaction() as conn:
            yield HealingCasesRepository(_BoundConnection(conn))

    # ------------------------------------------------------------------ writes

    async def create_with_history(self, columns: dict[str, Any]) -> dict[str, Any] | None:
        """Insert a case plus its ``opened`` history row.

        ``columns`` are the caller's values for the new row (the id is
        assigned here); the ``opened`` event stores all of them, so the row
        can be rebuilt from the log. Returns None when a case already owns
        this watcher run, or an OPEN case already owns this tracker ticket
        (the unique indexes from migration 69) — the caller looks that one
        up. Raises ``asyncpg.ForeignKeyViolationError`` for a watcher run or
        parent case that does not exist.
        """
        columns = {"id": f"hc-{uuid.uuid4().hex[:12]}", **columns}
        names = list(columns)
        async with self._db.transaction() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO healing_cases ({", ".join(names)})
                VALUES ({", ".join(f"${i}" for i in range(1, len(names) + 1))})
                ON CONFLICT DO NOTHING
                RETURNING *
                """,
                *columns.values(),
            )
            if row is None:
                return None
            # Every column as stored, so defaults the caller did not name
            # (none today) could never make the log disagree with the row.
            stored = {
                k: (v.isoformat() if isinstance(v, datetime) else v)
                for k, v in dict(row).items()
                if k not in ("created_at", "updated_at")
            }
            await LockedCase(conn, dict(row)).add_event(
                "opened", None, row["stage"], {"updates": stored, "effect": "applied"}, row["detected_at"]
            )
        logger.info(
            "healing_case_opened", case_id=row["id"], repo=row["repo"], origin=row["origin"], stage=row["stage"]
        )
        return dict(row)

    @asynccontextmanager
    async def locked(self, case_id: str) -> AsyncIterator[LockedCase | None]:
        """Hold one case ``FOR UPDATE`` for the duration of the block.

        Yields None when the case does not exist. Concurrent writers to the
        same case queue here, so each resolves its event against the stage
        the previous one committed.
        """
        async with self._db.transaction() as conn:
            row = await conn.fetchrow("SELECT * FROM healing_cases WHERE id = $1 FOR UPDATE", case_id)
            yield LockedCase(conn, dict(row)) if row is not None else None

    # ------------------------------------------------------------------- reads

    async def get(self, case_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow("SELECT * FROM healing_cases WHERE id = $1", case_id)
        return dict(row) if row else None

    async def get_detail(self, case_id: str) -> dict[str, Any] | None:
        """The case plus its jobs, PRs and history, each oldest first."""
        case = await self.get(case_id)
        if case is None:
            return None
        jobs = await self._db.fetch(
            """
            SELECT j.job_id, j.status, j.job_kind, j.pr_url, j.error_code,
                   j.created_at, j.finished_at, l.attached_at
            FROM healing_case_jobs l JOIN jobs j ON j.job_id = l.job_id
            WHERE l.case_id = $1
            ORDER BY j.created_at, j.job_id
            """,
            case_id,
        )
        prs = await self._db.fetch(
            """
            SELECT url, number, head_sha, state, merge_sha, job_id, opened_at, merged_at, closed_at
            FROM healing_case_prs WHERE case_id = $1 ORDER BY id
            """,
            case_id,
        )
        events = await self._db.fetch(
            """
            SELECT id, event, event_key, from_stage, to_stage, detail, occurred_at
            FROM healing_case_events WHERE case_id = $1 ORDER BY id
            """,
            case_id,
        )
        return {
            **case,
            "jobs": [dict(j) for j in jobs],
            "prs": [dict(p) for p in prs],
            "events": [dict(e) for e in events],
        }

    async def find_by_watcher_run(self, watcher_run_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow("SELECT * FROM healing_cases WHERE watcher_run_id = $1", watcher_run_id)
        return dict(row) if row else None

    async def find_by_tracker(self, tracker: str, tracker_key: str, repo: str | None = None) -> dict[str, Any] | None:
        """The ticket's open case (there is at most one per repo), else its
        newest closed one.

        ``repo`` matters for GitHub and GitLab, whose issue numbers repeat
        across repos — issue #42 exists in every repo that has 42 issues.
        Linear identifiers are globally unique, so its callers may omit it.
        """
        row = await self._db.fetchrow(
            """
            SELECT * FROM healing_cases
             WHERE tracker = $1 AND tracker_key = $2 AND ($3::text IS NULL OR repo = $3)
            ORDER BY (stage <> 'closed') DESC, created_at DESC, id DESC LIMIT 1
            """,
            tracker,
            tracker_key,
            repo,
        )
        return dict(row) if row else None

    async def find_by_issue(self, issue_id: str) -> dict[str, Any] | None:
        """The issue's open case if any, else its newest."""
        row = await self._db.fetchrow(
            """
            SELECT * FROM healing_cases WHERE issue_id = $1
            ORDER BY (stage <> 'closed') DESC, created_at DESC, id DESC LIMIT 1
            """,
            issue_id,
        )
        return dict(row) if row else None

    async def find_by_job(self, job_id: str) -> dict[str, Any] | None:
        """The case this job is linked to — its open one if it has several."""
        row = await self._db.fetchrow(
            """
            SELECT c.* FROM healing_case_jobs l JOIN healing_cases c ON c.id = l.case_id
            WHERE l.job_id = $1
            ORDER BY (c.stage <> 'closed') DESC, l.attached_at DESC, c.id DESC LIMIT 1
            """,
            job_id,
        )
        return dict(row) if row else None

    async def find_by_pr_url(self, pr_url: str) -> dict[str, Any] | None:
        """The case that owns this PR — any PR it ever had, not just its latest."""
        row = await self._db.fetchrow(
            "SELECT c.* FROM healing_case_prs p JOIN healing_cases c ON c.id = p.case_id WHERE p.url = $1",
            pr_url,
        )
        return dict(row) if row else None

    async def list_page(
        self,
        *,
        repo: str | None = None,
        stage: str | None = None,
        origin: str | None = None,
        resolution_path: str | None = None,
        healed_verdict: str | None = None,
        fix_deployed: bool | None = None,
        open_only: bool = False,
        awaiting_decision: bool = False,
        limit: int = 50,
        after: tuple[datetime, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """One keyset page, newest first. Returns ``(page, total matching the filters)``.

        ``awaiting_decision`` keeps only cases waiting for a person's answer
        (``_AWAITING_DECISION``): watcher drafts nobody has accepted or
        rejected, cases a ``not_healed`` verdict sent back for a retry nobody
        has accepted yet, and organization needs (origin ``org``) waiting for
        a person. Not ``stage = 'tracked'``, which also holds a
        human's ticket mid-dispatch and a draft whose ticket was finished or
        deleted in its tracker.

        ``after`` is the ``(created_at, id)`` of the last row of the previous
        page. ``(created_at, id)`` is a total order, so pages never repeat or
        drop a row — not when a backfill stamps many rows in one instant, and
        not when cases are opened between two page requests.
        """
        conditions: list[str] = []
        args: list[Any] = []
        filters = (
            ("repo", repo),
            ("stage", stage),
            ("origin", origin),
            ("resolution_path", resolution_path),
            ("healed_verdict", healed_verdict),
        )
        for column, value in filters:
            if value:
                args.append(value)
                conditions.append(f"c.{column} = ${len(args)}")
        if fix_deployed is not None:
            conditions.append(_FIX_DEPLOYED if fix_deployed else f"NOT {_FIX_DEPLOYED}")
        if open_only:
            conditions.append("c.stage <> 'closed'")
        if awaiting_decision:
            conditions.append(_AWAITING_DECISION)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = await self._db.fetchval(f"SELECT COUNT(*) FROM healing_cases c {where}", *args)

        if after is not None:
            args.extend(after)
            conditions.append(f"(c.created_at, c.id) < (${len(args) - 1}, ${len(args)})")
            where = f"WHERE {' AND '.join(conditions)}"
        rows = await self._db.fetch(
            f"""
            SELECT c.*, pr.*,
                   (SELECT COUNT(*) FROM healing_case_jobs l WHERE l.case_id = c.id) AS jobs_count,
                   (SELECT COUNT(*) FROM healing_case_prs p WHERE p.case_id = c.id) AS prs_count
            FROM healing_cases c {_CURRENT_PR} {where}
            ORDER BY c.created_at DESC, c.id DESC
            LIMIT ${len(args) + 1}
            """,
            *args,
            limit,
        )
        return [dict(r) for r in rows], int(total or 0)

    async def count_open_unaccepted_watcher_cases(self, *, days: int, repo: str | None = None) -> int:
        """How many watcher drafts still await a human — the proposal cap.
        See ``_CAP_PREDICATE`` for the rule and why each clause is there."""
        total = await self._db.fetchval(
            f"SELECT COUNT(*) FROM healing_cases c WHERE {_CAP_PREDICATE}", list(CAP_STAGES), days, repo
        )
        return int(total or 0)

    async def metrics_snapshot(self) -> dict[str, Any]:
        """Aggregates for the Prometheus gauges (#285) — counts only, no case data.

        ``open``: open cases per (stage, origin). ``oldest_age_seconds``: per
        stage, how long the longest-waiting open case has been in it, measured
        from ``<stage>_at`` (a case whose PR closed unmerged goes back to
        ``accepted`` with its first timestamp, so its age there includes the
        round trip; a reopen restarts the wait: it stamps ``tracked_at`` and
        clears ``accepted_at``, so a re-acceptance is aged from itself).
        ``awaiting_verdict``: open cases with a deployed fix and no verdict
        yet. ``closed``: closed cases per resolution path.
        """
        entered = " ".join(f"WHEN '{stage}' THEN c.{stage}_at" for stage in OPEN_STAGES)
        rows = await self._db.fetch(
            f"""
            SELECT c.stage, c.origin, COUNT(*) AS count,
                   EXTRACT(EPOCH FROM NOW() - MIN(COALESCE(CASE c.stage {entered} END, c.detected_at))) AS oldest_age
            FROM healing_cases c
            WHERE c.stage <> 'closed'
            GROUP BY c.stage, c.origin
            """
        )
        oldest: dict[str, float] = {}
        for row in rows:
            oldest[row["stage"]] = max(oldest.get(row["stage"], 0.0), float(row["oldest_age"] or 0.0))
        awaiting = await self._db.fetchval(
            f"SELECT COUNT(*) FROM healing_cases c "
            f"WHERE c.stage <> 'closed' AND c.healed_verdict = 'pending' AND {_FIX_DEPLOYED}"
        )
        closed = await self._db.fetch(
            "SELECT COALESCE(c.resolution_path, '') AS resolution_path, COUNT(*) AS count "
            "FROM healing_cases c WHERE c.stage = 'closed' GROUP BY 1"
        )
        return {
            "open": [{"stage": r["stage"], "origin": r["origin"], "count": int(r["count"])} for r in rows],
            "oldest_age_seconds": oldest,
            "awaiting_verdict": int(awaiting or 0),
            "closed": {r["resolution_path"]: int(r["count"]) for r in closed},
        }

    async def open_unaccepted_watcher_tracker_keys(self, *, days: int | None, repo: str | None = None) -> list[str]:
        """Tracker keys of open watcher drafts.

        ``days`` is the cap window: exactly the cases the cap counts. ``None``
        drops the window, which is what reconcile uses — a case outlives the
        cap, and a ticket cancelled after the window must still close (#415).
        The clauses other than the window are ``_CAP_PREDICATE``.
        """
        if days is None:
            rows = await self._db.fetch(
                """
                SELECT c.tracker_key FROM healing_cases c
                WHERE c.origin = 'watcher'
                  AND c.tracker_key IS NOT NULL
                  AND c.stage = ANY($1::text[])
                  AND ($2::text IS NULL OR c.repo = $2)
                  AND NOT EXISTS (
                      SELECT 1 FROM watcher_runs w
                      WHERE w.id = c.watcher_run_id AND w.released_at IS NOT NULL
                  )
                ORDER BY c.detected_at
                """,
                list(CAP_STAGES),
                repo,
            )
            return [str(r["tracker_key"]) for r in rows]
        rows = await self._db.fetch(
            f"SELECT c.tracker_key FROM healing_cases c WHERE {_CAP_PREDICATE} ORDER BY c.detected_at",
            list(CAP_STAGES),
            days,
            repo,
        )
        return [str(r["tracker_key"]) for r in rows]

    async def find_by_fingerprint(
        self, fingerprint: str, *, origin: str, repo: str | None = None
    ) -> dict[str, Any] | None:
        """The newest case with this dedup fingerprint — an open one first."""
        row = await self._db.fetchrow(
            """
            SELECT * FROM healing_cases
            WHERE fingerprint = $1 AND origin = $2 AND ($3::text IS NULL OR repo = $3)
            ORDER BY (stage <> 'closed') DESC, created_at DESC, id DESC LIMIT 1
            """,
            fingerprint,
            origin,
            repo,
        )
        return dict(row) if row else None

    async def find_by_signature(
        self, signature_id: str, *, repo: str | None = None, origin: str | None = None
    ) -> dict[str, Any] | None:
        """The case this log signature already belongs to, open one first (#290).

        Three deliberate differences from :meth:`find_by_fingerprint`, each one
        something the title-hash dedupe got wrong (it matched 0 times in 1,339
        ticks):

        * **No time bound.** A fault that recurs after eight days is the same
          fault. The old check expired at ``watcher_proposal_window_days``, so
          ``LazyInitializationException`` was filed three separate times.
        * **Every open stage**, not just the cap stages — a case with a PR open
          is still where a recurrence belongs.
        * **Origin is optional.** A signature seen by the watcher and by an
          Alertmanager rule (#296) is one fault; the caller decides whether to
          narrow.

        Open cases sort before closed ones and the newest wins within each
        group, so a caller handed a closed row knows there is no open one —
        that is the regression-link case.
        """
        row = await self._db.fetchrow(
            """
            SELECT * FROM healing_cases
            WHERE signature_id = $1
              AND ($2::text IS NULL OR repo = $2)
              AND ($3::text IS NULL OR origin = $3)
            ORDER BY (stage <> 'closed') DESC, created_at DESC, id DESC LIMIT 1
            """,
            signature_id,
            repo,
            origin,
        )
        return dict(row) if row else None

    async def open_signature_ids(self, *, repo: str | None = None) -> set[str]:
        """Signature ids that currently have an open case.

        One round trip for a whole window: #291's skip decision asks this of
        every notable signature at once, and a query per signature would put
        the cost back where that issue is trying to take it from.
        """
        rows = await self._db.fetch(
            """
            SELECT DISTINCT signature_id FROM healing_cases
            WHERE signature_id IS NOT NULL
              AND stage = ANY($1::text[])
              AND ($2::text IS NULL OR repo = $2)
            """,
            list(OPEN_STAGES),
            repo,
        )
        return {r["signature_id"] for r in rows}

    async def deferred_watcher_findings(self, *, repo: str | None = None) -> list[dict[str, Any]]:
        """Watcher findings that were detected while the draft cap was full and
        have no ticket yet (#283), oldest first.

        They hold no cap slot (``_CAP_PREDICATE`` needs a tracker key). Each
        row carries its watcher run's ``run_action``, ``run_identifier`` and
        ``run_job_id``: the analysis job is where the proposal text lives —
        the ledger never stores it — and a run that already names a ticket
        marks a filing whose ledger write was lost.
        """
        rows = await self._db.fetch(
            """
            SELECT c.*, w.action AS run_action, w.tracker_key AS run_identifier, w.tracker AS run_tracker,
                   w.job_id AS run_job_id, w.window_start AS run_window_start, w.window_end AS run_window_end
            FROM healing_cases c JOIN watcher_runs w ON w.id = c.watcher_run_id
            WHERE c.origin = 'watcher' AND c.stage = 'detected' AND c.tracker_key IS NULL
              AND ($1::text IS NULL OR c.repo = $1)
            ORDER BY c.detected_at, c.id
            """,
            repo,
        )
        return [dict(r) for r in rows]

    async def awaiting_verdict(self, *, repo: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Cases whose fix is running but which nobody has confirmed (#312).

        `deployed_at` is the anchor, not `stage`: a case can reach `verified`
        or `closed` without any fix of ours — it resolved itself, or somebody
        fixed it outside the code — and those still deserve a verdict, which is
        this issue's "runs for out-of-band fixes too" criterion. `_FIX_DEPLOYED`
        is the same predicate the API's `fix_deployed` field and its filter
        already use, so the sweep and the dashboard cannot disagree about which
        cases these are.

        Oldest deploy first: the case that has waited longest for an answer is
        the one most likely to have one.
        """
        rows = await self._db.fetch(
            f"""
            SELECT c.id, c.repo, c.title, c.signature_id, c.signature, c.signature_source, c.tracker, c.tracker_key,
                   c.tracker_url, c.stage, c.merge_sha, c.merged_at, c.deployed_at,
                   c.deploy_sha, c.deploy_marker_at, c.healed_verdict, c.occurrence_count,
                   {_REOPENS} AS reopens
              FROM healing_cases c
             WHERE c.healed_verdict = 'pending'
               AND {_FIX_DEPLOYED}
               AND ($1::text IS NULL OR c.repo = $1)
             ORDER BY c.deployed_at, c.id
             LIMIT $2
            """,  # noqa: S608 - _FIX_DEPLOYED is a module constant, never input
            repo,
            limit,
        )
        return [dict(r) for r in rows]

    async def not_healed_awaiting_retry(
        self, *, max_reopens: int, repo: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Cases whose fix was judged ``not_healed`` and that may still be
        reopened for another attempt (#451).

        A case qualifies while it sits at ``verified`` with that verdict and
        has been reopened fewer than ``max_reopens`` times. Counted from the
        case's own history, so a restart does not refill the budget. A case
        past the budget is left where it is: the verdict that spent it said so
        in its evidence (``ConfirmHealedSweep``).
        """
        rows = await self._db.fetch(
            f"""
            SELECT c.id, c.repo, c.title, c.tracker, c.tracker_key, c.deploy_sha,
                   c.deploy_marker_at, c.deployed_at, {_REOPENS} AS reopens
              FROM healing_cases c
             WHERE c.stage = 'verified'
               AND c.healed_verdict = 'not_healed'
               AND {_REOPENS} < $1
               AND ($2::text IS NULL OR c.repo = $2)
             ORDER BY c.verdict_checked_at, c.id
             LIMIT $3
            """,  # noqa: S608 - _REOPENS is a module constant, never input
            max_reopens,
            repo,
            limit,
        )
        return [dict(r) for r in rows]

    async def awaiting_deploy(self, *, repo: str) -> list[dict[str, Any]]:
        """Open cases of ``repo`` whose merge commit is known — the ones a
        deploy marker can be checked against. Oldest merge first."""
        rows = await self._db.fetch(
            """
            SELECT id, merge_sha, merged_at, deploy_sha, deploy_marker_at FROM healing_cases
            WHERE repo = $1 AND stage <> 'closed' AND merge_sha IS NOT NULL AND merged_at IS NOT NULL
            ORDER BY merged_at, id
            """,
            repo,
        )
        return [dict(r) for r in rows]

    async def watcher_proposal_chains(self) -> list[dict[str, Any]]:
        """Every watcher proposal with the issue and jobs it led to, oldest first.

        Input for the historical backfill. The proposal → issue hop joins on
        the issue's real tracker reference (``tracker='linear'`` +
        ``tracker_key``), never on the title. A proposal whose ticket id was
        erased (pre-#278 reconcile) therefore joins to nothing, by design.
        Each row carries ``jobs``: the issue's jobs, oldest first.
        """
        runs = await self._db.fetch(
            """
            SELECT w.id AS watcher_run_id, w.detail, w.fingerprint, w.tracker, w.tracker_key, w.repo AS run_repo,
                   w.created_at AS proposed_at,
                   i.id AS issue_id, i.repo AS issue_repo, i.title AS issue_title,
                   i.tracker_url AS issue_tracker_url, i.created_at AS issue_created_at
            FROM watcher_runs w
            LEFT JOIN LATERAL (
                SELECT * FROM issues i
                WHERE i.tracker = COALESCE(w.tracker, 'linear') AND i.tracker_key = w.tracker_key
                ORDER BY i.created_at, i.id LIMIT 1
            ) i ON TRUE
            WHERE w.action = 'proposed'
            ORDER BY w.created_at, w.id
            """
        )
        issue_ids = [r["issue_id"] for r in runs if r["issue_id"] is not None]
        jobs = await self._db.fetch(
            """
            SELECT job_id, issue_id, status, pr_url, created_at, finished_at, updated_at
            FROM jobs WHERE issue_id = ANY($1::text[])
            ORDER BY created_at, job_id
            """,
            issue_ids,
        )
        by_issue: dict[str, list[dict[str, Any]]] = {}
        for job in jobs:
            by_issue.setdefault(job["issue_id"], []).append(dict(job))
        return [{**dict(r), "jobs": by_issue.get(r["issue_id"], [])} for r in runs]
