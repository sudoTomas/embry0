"""Watcher-runs repository — audit trail for the log-watcher/proposer (RAV-657)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import structlog

from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

WATCHER_ACTIONS = frozenset({"skipped_quiet", "skipped_backlog", "skipped_duplicate", "no_issue", "proposed", "error"})


class WatcherRunsRepository:
    """Insert/read operations for the watcher_runs table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def create(
        self,
        window_start: datetime,
        window_end: datetime,
        action: str,
        log_lines: int = 0,
        detail: str | None = None,
        job_id: str | None = None,
        fingerprint: str | None = None,
        linear_issue_identifier: str | None = None,
    ) -> str:
        if action not in WATCHER_ACTIONS:
            raise ValueError(f"unknown watcher action {action!r} (expected one of {sorted(WATCHER_ACTIONS)})")
        run_id = f"wr-{uuid.uuid4().hex[:12]}"
        await self._db.execute(
            """
            INSERT INTO watcher_runs (
                id, window_start, window_end, log_lines, action, detail,
                job_id, fingerprint, linear_issue_identifier
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            run_id,
            window_start,
            window_end,
            log_lines,
            action,
            detail,
            job_id,
            fingerprint,
            linear_issue_identifier,
        )
        logger.info("watcher_run_recorded", run_id=run_id, action=action, log_lines=log_lines)
        return run_id

    async def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT * FROM watcher_runs ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        return [dict(r) for r in rows]

    async def open_proposal_count(self) -> int:
        """Proposals filed by the watcher (drafts awaiting human review).

        A draft leaves 'open' when the human accepts it — acceptance means
        labeling the Linear ticket `embry0`, which dispatches a pipeline run;
        we approximate 'still open' as 'proposed and never dispatched', i.e.
        no embry0 issue exists whose title carries the draft's identifier.
        """
        row = await self._db.fetchrow(
            """
            SELECT COUNT(*) AS n FROM watcher_runs w
            WHERE w.action = 'proposed'
              AND w.linear_issue_identifier IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM issues i
                  WHERE i.title LIKE '[' || w.linear_issue_identifier || ']%'
              )
            """
        )
        return int(row["n"]) if row else 0

    async def fingerprint_exists(self, fingerprint: str) -> bool:
        row = await self._db.fetchrow(
            "SELECT 1 FROM watcher_runs WHERE fingerprint = $1 AND action = 'proposed' LIMIT 1",
            fingerprint,
        )
        return row is not None
