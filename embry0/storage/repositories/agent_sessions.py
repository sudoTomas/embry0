"""Per-(job, agent_type) session storage for resumable agent conversations."""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)


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
