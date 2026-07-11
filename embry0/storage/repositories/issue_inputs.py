"""IssueInputsRepository — PostgreSQL CRUD for human-in-the-loop questions."""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)


class IssueInputsRepository:
    """CRUD operations for the issue_inputs table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def create(
        self,
        issue_id: str,
        job_id: str,
        question: str,
        asking_node: str = "triage",
        importance: str = "blocking",
        auto_answer: str | None = None,
    ) -> str:
        """Create a new issue input and return its ID.

        If auto_answer is provided the status is set to 'auto_answered';
        otherwise the status is 'pending'.
        """
        input_id = f"inp-{uuid.uuid4().hex[:12]}"
        status = "auto_answered" if auto_answer is not None and importance == "auto_answerable" else "pending"
        await self._db.execute(
            """
            INSERT INTO issue_inputs (
                id, issue_id, job_id, asking_node, question,
                importance, auto_answer, status
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            input_id,
            issue_id,
            job_id,
            asking_node,
            question,
            importance,
            auto_answer,
            status,
        )
        logger.info("issue_input_created", input_id=input_id, issue_id=issue_id, status=status)
        return input_id

    async def get(self, input_id: str) -> dict[str, Any] | None:
        """Fetch a single issue input by ID."""
        row = await self._db.fetchrow(
            "SELECT * FROM issue_inputs WHERE id = $1",
            input_id,
        )
        if row is None:
            return None
        return dict(row)

    async def list_by_issue(self, issue_id: str) -> list[dict[str, Any]]:
        """Return all inputs for an issue, ordered by created_at ASC."""
        rows = await self._db.fetch(
            "SELECT * FROM issue_inputs WHERE issue_id = $1 ORDER BY created_at ASC",
            issue_id,
        )
        return [dict(r) for r in rows]

    async def list_by_job(self, job_id: str) -> list[dict[str, Any]]:
        """Return all inputs for a job."""
        rows = await self._db.fetch(
            "SELECT * FROM issue_inputs WHERE job_id = $1 ORDER BY created_at ASC",
            job_id,
        )
        return [dict(r) for r in rows]

    async def answer(
        self,
        input_id: str,
        answer: str,
        answered_by: str,
    ) -> None:
        """Record an answer and mark the input as answered."""
        await self._db.execute(
            """
            UPDATE issue_inputs
            SET answer = $1,
                answered_by = $2,
                status = 'answered',
                answered_at = NOW()
            WHERE id = $3
            """,
            answer,
            answered_by,
            input_id,
        )
        logger.info("issue_input_answered", input_id=input_id, answered_by=answered_by)

    async def skip(
        self,
        input_id: str,
        skipped_by: str,
    ) -> None:
        """Record a skip and mark the input as skipped.

        Used by the inbound dispatcher when a comment includes `/skip N`.
        Mirrors `answer()` — sets status='skipped' and records who skipped
        and when. Workflow downstream may continue past it (if non-blocking)
        or escalate.
        """
        await self._db.execute(
            """
            UPDATE issue_inputs
            SET status = 'skipped',
                skipped_by = $1,
                skipped_at = NOW()
            WHERE id = $2
            """,
            skipped_by,
            input_id,
        )
        logger.info("issue_input_skipped", input_id=input_id, skipped_by=skipped_by)

    async def list_pending_for_issue(self, issue_id: str) -> list[dict[str, Any]]:
        """Return pending inputs for an issue in stable creation order.

        Sequence numbers in /answer N: are 1-based indexes into this list.
        """
        rows = await self._db.fetch(
            "SELECT * FROM issue_inputs WHERE issue_id = $1 AND status = 'pending' ORDER BY created_at ASC, id ASC",
            issue_id,
        )
        return [dict(r) for r in rows]

    async def find_by_issue_and_sequence(self, issue_id: str, sequence: int) -> dict[str, Any] | None:
        """Lookup helper for the inbound parser. sequence is 1-based."""
        rows = await self.list_pending_for_issue(issue_id)
        if sequence < 1 or sequence > len(rows):
            return None
        return rows[sequence - 1]

    async def set_telegram_message_id(self, input_id: str, message_id: int) -> None:
        """Store the Telegram message ID associated with this input."""
        await self._db.execute(
            "UPDATE issue_inputs SET telegram_message_id = $1 WHERE id = $2",
            message_id,
            input_id,
        )
        logger.info("issue_input_telegram_msg_set", input_id=input_id, message_id=message_id)

    async def get_by_telegram_message(self, message_id: int) -> dict[str, Any] | None:
        """Fetch an input by its associated Telegram message ID."""
        row = await self._db.fetchrow(
            "SELECT * FROM issue_inputs WHERE telegram_message_id = $1",
            message_id,
        )
        if row is None:
            return None
        return dict(row)

    async def count_pending_blocking(self, issue_id: str) -> int:
        """Return the count of blocking inputs still in pending status."""
        result = await self._db.fetchval(
            """
            SELECT COUNT(*)
            FROM issue_inputs
            WHERE issue_id = $1
              AND importance = 'blocking'
              AND status = 'pending'
            """,
            issue_id,
        )
        return int(result or 0)

    async def list_all_answered(self, issue_id: str) -> list[dict[str, Any]]:
        """Return all answered or auto_answered inputs for an issue."""
        rows = await self._db.fetch(
            """
            SELECT * FROM issue_inputs
            WHERE issue_id = $1
              AND status IN ('answered', 'auto_answered')
            ORDER BY created_at ASC
            """,
            issue_id,
        )
        return [dict(r) for r in rows]
