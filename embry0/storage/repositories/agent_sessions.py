"""Per-(job, agent_type) session storage for resumable agent conversations."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import structlog

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.jobs import RELEASED_JOB_STATUSES

logger = structlog.get_logger(__name__)

# What a saved session holds: the CLI session file, or the message history.
_SESSION_BYTES = "COALESCE(octet_length(s.session_blob), 0) + COALESCE(octet_length(s.messages::text), 0)"


class AgentSessionsRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def upsert(
        self,
        *,
        job_id: str,
        agent_type: str,
        mode: str,
        session_id: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        session_blob: bytes | None = None,
    ) -> None:
        sid = f"asess-{uuid.uuid4().hex[:12]}"
        await self._db.execute(
            """
            INSERT INTO agent_sessions (id, job_id, agent_type, mode, session_id, messages, session_blob, last_turn_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, now())
            ON CONFLICT (job_id, agent_type) DO UPDATE SET
                mode         = EXCLUDED.mode,
                session_id   = EXCLUDED.session_id,
                messages     = EXCLUDED.messages,
                session_blob = EXCLUDED.session_blob,
                last_turn_at = now()
            """,
            sid,
            job_id,
            agent_type,
            mode,
            session_id,
            messages,
            session_blob,
        )

    async def get(self, job_id: str, agent_type: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            "SELECT * FROM agent_sessions WHERE job_id=$1 AND agent_type=$2",
            job_id,
            agent_type,
        )
        if row is None:
            return None
        return dict(row)

    async def delete(self, job_id: str, agent_type: str) -> None:
        await self._db.execute(
            "DELETE FROM agent_sessions WHERE job_id=$1 AND agent_type=$2",
            job_id,
            agent_type,
        )

    async def delete_for_job(self, job_id: str) -> None:
        await self._db.execute("DELETE FROM agent_sessions WHERE job_id=$1", job_id)

    # --- retention (ADR 0017, embry0/services/agent_session_retention.py) ------
    # A saved session is the agent's whole conversation: for a Claude
    # subscription the CLI's session file, every tool input and result in it
    # uncapped. It lets a later turn of the same job, or a pull request comment
    # follow-up, resume instead of starting cold.

    async def expired(self, *, cutoff: datetime) -> tuple[int, int]:
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

    async def delete_expired(self, *, cutoff: datetime, limit: int) -> tuple[int, int]:
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
