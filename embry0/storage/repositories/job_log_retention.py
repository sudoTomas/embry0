"""The queries behind job log retention (ADR 0017, ``embry0/services/job_log_retention.py``).

``job_logs.event_type`` is filled only for rows written since migration 95;
older rows carry the type only inside their JSON text, so every filter here
also reads the text. ``json.dumps`` writes ``"type": "tool_call"`` with exactly
that spacing, and inside a JSON string the quotes would be escaped, so the
pattern only matches the event's own type.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.jobs import RELEASED_JOB_STATUSES

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# A tool event of either type: the column when it is filled, the text when not.
_TOOL_EVENT = """
    (l.event_type IN ('tool_call', 'tool_result')
     OR (l.event_type IS NULL
         AND (l.text LIKE '%"type": "tool_call"%' OR l.text LIKE '%"type": "tool_result"%')))
"""

# A tool event that still carries code: any result, or a call of a tool in $code_tools.
_CODE_EVENT = """
    ((COALESCE(l.event_type, 'tool_result') = 'tool_result' AND l.text LIKE '%"type": "tool_result"%')
     OR (COALESCE(l.event_type, 'tool_call') = 'tool_call' AND l.text LIKE '%"type": "tool_call"%'
         AND l.text ~ ('"tool_name": "(' || array_to_string($5::text[], '|') || ')"')))
"""


# What a saved session holds: the CLI session file, or the message history.
_SESSION_BYTES = "COALESCE(octet_length(s.session_blob), 0) + COALESCE(octet_length(s.messages::text), 0)"


@dataclass(frozen=True, slots=True)
class CandidateJob:
    job_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LogRow:
    id: int
    created_at: datetime
    text: str


class JobLogRetentionRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def candidate_jobs(
        self,
        *,
        cutoff: datetime,
        after: tuple[datetime, str] | None,
        limit: int,
        code_tools: Sequence[str],
        marker: str,
    ) -> list[CandidateJob]:
        """Ended jobs, in (created_at, job_id) order after ``after``, holding a code event older than ``cutoff``.

        ``marker`` is the text a redacted event carries; such events no longer count.
        """
        after_at, after_id = after or (_EPOCH, "")
        rows = await self._db.fetch(
            f"""
            SELECT j.job_id, j.created_at FROM jobs j
            WHERE j.status = ANY($1::text[])
              AND j.created_at < $2
              AND (j.created_at, j.job_id) > ($3, $4)
              AND EXISTS (
                SELECT 1 FROM job_logs l
                WHERE l.job_id = j.job_id AND l.stream = 'pipeline' AND l.created_at < $2
                  AND l.text NOT LIKE ('%' || $6 || '%')
                  AND {_CODE_EVENT}
              )
            ORDER BY j.created_at, j.job_id
            LIMIT $7
            """,
            sorted(str(status) for status in RELEASED_JOB_STATUSES),
            cutoff,
            after_at,
            after_id,
            list(code_tools),
            marker,
            limit,
        )
        return [CandidateJob(job_id=r["job_id"], created_at=r["created_at"]) for r in rows]

    async def tool_events(self, job_id: str) -> list[LogRow]:
        """Every tool event of the job, oldest first."""
        rows = await self._db.fetch(
            f"""
            SELECT l.id, l.created_at, l.text FROM job_logs l
            WHERE l.job_id = $1 AND l.stream = 'pipeline' AND {_TOOL_EVENT}
            ORDER BY l.id
            """,
            job_id,
        )
        return [LogRow(id=r["id"], created_at=r["created_at"], text=r["text"]) for r in rows]

    async def rewrite(self, rows: Sequence[tuple[int, str]]) -> None:
        """Replace the stored text of each ``(id, text)`` pair, in one transaction."""
        if not rows:
            return
        async with self._db.transaction() as conn:
            await conn.executemany("UPDATE job_logs SET text = $2 WHERE id = $1", list(rows))

    # --- agent sessions -------------------------------------------------------
    # A saved session is the agent's whole conversation: for a Claude
    # subscription the CLI's session file, every tool input and result in it
    # uncapped. It lets a later turn of the same job, or a pull request comment
    # follow-up, resume instead of starting cold.

    async def expired_sessions(self, *, cutoff: datetime) -> tuple[int, int]:
        """``(sessions, bytes)`` of ended jobs' sessions last used before ``cutoff``."""
        row = await self._db.fetchrow(
            f"""
            SELECT count(*) AS n, COALESCE(sum({_SESSION_BYTES}), 0) AS bytes
            FROM agent_sessions s JOIN jobs j ON j.job_id = s.job_id
            WHERE j.status = ANY($1::text[]) AND s.last_turn_at < $2
            """,
            sorted(str(status) for status in RELEASED_JOB_STATUSES),
            cutoff,
        )
        return (int(row["n"]), int(row["bytes"])) if row is not None else (0, 0)

    async def delete_sessions(self, *, cutoff: datetime, limit: int) -> tuple[int, int]:
        """Delete those sessions, ``limit`` per statement; returns ``(sessions, bytes)`` deleted."""
        deleted = removed = 0
        while True:
            rows = await self._db.fetch(
                f"""
                DELETE FROM agent_sessions AS s WHERE s.id IN (
                    SELECT x.id FROM agent_sessions x JOIN jobs j ON j.job_id = x.job_id
                    WHERE j.status = ANY($1::text[]) AND x.last_turn_at < $2
                    ORDER BY x.last_turn_at
                    LIMIT $3
                )
                RETURNING {_SESSION_BYTES} AS bytes
                """,
                sorted(str(status) for status in RELEASED_JOB_STATUSES),
                cutoff,
                limit,
            )
            if not rows:
                return deleted, removed
            deleted += len(rows)
            removed += sum(int(r["bytes"]) for r in rows)
