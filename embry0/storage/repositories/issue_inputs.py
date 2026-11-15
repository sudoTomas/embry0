"""IssueInputsRepository — PostgreSQL CRUD for human-in-the-loop questions."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import structlog

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.jobs import job_repository_sql

logger = structlog.get_logger(__name__)


def _covered(repos: str) -> str:
    """Whether the question ``x`` is of the repositories in the ``text[]`` placeholder ``repos`` (lower-cased slugs;
    NULL keeps every question): its job works on one of them (:func:`job_repository_sql`) and, when it is asked
    about an issue, the issue is of one of them. A question belongs to both."""
    return f"""({repos}::text[] IS NULL OR (
        EXISTS (SELECT 1 FROM jobs j WHERE j.job_id = x.job_id
                AND lower({job_repository_sql("j")}) = ANY({repos}::text[]))
        AND (x.issue_id IS NULL OR EXISTS (SELECT 1 FROM issues i WHERE i.id = x.issue_id
                                           AND lower(i.repo) = ANY({repos}::text[])))))"""


class IssueInputsRepository:
    """CRUD operations for the issue_inputs table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def create(
        self,
        issue_id: str | None,
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

    async def record_auto_answer(
        self,
        *,
        input_id: str,
        issue_id: str | None,
        job_id: str,
        question: str,
        asking_node: str,
        auto_answer: str | None,
    ) -> bool:
        """Record a question an agent asked and answered itself (``importance="auto_answerable"``) as ``input_id``.

        The workflow did not wait for it, so the row is ``auto_answered`` whether or not the agent named an
        answer: it is the record of what the agent went on with, and a person can still answer it (#410).
        ``input_id`` is the one the node gave the question, so the same question streamed again (a node's update
        replayed on a resume) writes nothing: True when the row was written, False when it was there already.
        """
        written = await self._db.fetchval(
            """
            INSERT INTO issue_inputs (
                id, issue_id, job_id, asking_node, question,
                importance, auto_answer, status
            )
            VALUES ($1, $2, $3, $4, $5, 'auto_answerable', $6, 'auto_answered')
            ON CONFLICT (id) DO NOTHING
            RETURNING id
            """,
            input_id,
            issue_id,
            job_id,
            asking_node,
            question,
            auto_answer,
        )
        if written is not None:
            logger.info("issue_input_auto_answered", input_id=input_id, job_id=job_id, asking_node=asking_node)
        return written is not None

    async def get(self, input_id: str) -> dict[str, Any] | None:
        """Fetch a single issue input by ID."""
        row = await self._db.fetchrow(
            "SELECT * FROM issue_inputs WHERE id = $1",
            input_id,
        )
        if row is None:
            return None
        return dict(row)

    async def list_by_issue(self, issue_id: str, *, repos: Sequence[str] | None = None) -> list[dict[str, Any]]:
        """Return all inputs for an issue, ordered by created_at ASC. ``repos`` (lower-cased slugs) keeps only the
        questions of those repositories: asked by a job working on one, about an issue of one."""
        rows = await self._db.fetch(
            f"SELECT x.* FROM issue_inputs x WHERE x.issue_id = $1 AND {_covered('$2')} ORDER BY x.created_at ASC, x.id ASC",
            issue_id,
            None if repos is None else list(repos),
        )
        return [dict(r) for r in rows]

    async def list_by_job(self, job_id: str, *, repos: Sequence[str] | None = None) -> list[dict[str, Any]]:
        """Return all inputs for a job. ``repos`` (lower-cased slugs) keeps only the questions of those
        repositories, as :meth:`list_by_issue` does."""
        rows = await self._db.fetch(
            f"SELECT x.* FROM issue_inputs x WHERE x.job_id = $1 AND {_covered('$2')} ORDER BY x.created_at ASC, x.id ASC",
            job_id,
            None if repos is None else list(repos),
        )
        return [dict(r) for r in rows]

    async def answer(
        self,
        input_id: str,
        answer: str,
        answered_by: str,
    ) -> bool:
        """Record an answer and mark the input as answered; False when it was answered already.

        One statement, so of two answers to one input given at once only the
        first is taken (#499). An input the orchestrator answered itself
        (``auto_answered``) still takes a person's answer (RAV-1059).
        """
        row = await self._db.fetchrow(
            """
            UPDATE issue_inputs
            SET answer = $1,
                answered_by = $2,
                status = 'answered',
                answered_at = NOW()
            WHERE id = $3 AND status IS DISTINCT FROM 'answered'
            RETURNING id
            """,
            answer,
            answered_by,
            input_id,
        )
        if row is None:
            logger.info("issue_input_already_answered", input_id=input_id, answered_by=answered_by)
            return False
        logger.info("issue_input_answered", input_id=input_id, answered_by=answered_by)
        return True

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

    async def set_github_comment_ref(self, input_id: str, comment_id: int, sequence: int) -> None:
        """Record which GitHub comment rendered this question, and where in it.

        This is the ``/answer N:`` anchor (#253): N is a position in a comment
        the author read, not in the live pending list, which other channels
        mutate underneath them.
        """
        await self._db.execute(
            "UPDATE issue_inputs SET github_comment_id = $1, github_comment_seq = $2 WHERE id = $3",
            comment_id,
            sequence,
            input_id,
        )
        logger.info(
            "issue_input_github_comment_ref_set",
            input_id=input_id,
            comment_id=comment_id,
            sequence=sequence,
        )

    async def get_by_github_comment_sequence(self, issue_id: str, sequence: int) -> dict[str, Any] | None:
        """The pending input at 1-based ``sequence`` of the issue's one
        outstanding question comment, or None when there isn't exactly one
        (#253).

        GitHub issue comments carry no ``in_reply_to``, so "the comment being
        replied to" is a guess. It is only a safe guess while every question
        still awaiting an answer lives in the same comment. Two conditions
        make it unsafe, and both resolve to None so the caller can ask for the
        stable id instead of writing the answer onto the wrong question:

        * pending questions mapped to more than one comment — replying to the
          older comment with ``/answer 1:`` would otherwise answer the newer
          comment's question 1;
        * a pending *blocking* question with no mapping at all — either its
          comment failed to post or the mapping write failed, so a comment the
          author may be reading right now is one we cannot resolve against.

        Unmapped non-blocking rows are ignored: they are never rendered to
        GitHub (only blocking questions are dispatched), so they cannot be
        what the author is looking at.
        """
        row = await self._db.fetchrow(
            """
            WITH pending AS (
                SELECT * FROM issue_inputs
                WHERE issue_id = $1 AND status = 'pending'
            ),
            unambiguous AS (
                SELECT 1
                WHERE (SELECT COUNT(DISTINCT github_comment_id) FROM pending) = 1
                  AND NOT EXISTS (
                      SELECT 1 FROM pending
                      WHERE github_comment_id IS NULL AND importance = 'blocking'
                  )
            )
            SELECT p.* FROM pending p, unambiguous
            WHERE p.github_comment_seq = $2
            """,
            issue_id,
            sequence,
        )
        if row is None:
            return None
        return dict(row)

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

    async def count_pending_blocking_for_job(self, job_id: str) -> int:
        """Pending blocking questions for a JOB — the issue-less-job answer
        path (EMB-43) gates resume on this instead of the issue-scoped count."""
        val = await self._db.fetchval(
            """
            SELECT COUNT(*) FROM issue_inputs
            WHERE job_id = $1 AND status = 'pending' AND importance = 'blocking'
            """,
            job_id,
        )
        return int(val or 0)

    async def list_all_answered_for_job(self, job_id: str) -> list[dict[str, Any]]:
        """Answered/auto-answered inputs for a JOB, oldest first (EMB-43)."""
        rows = await self._db.fetch(
            """
            SELECT * FROM issue_inputs
            WHERE job_id = $1 AND status IN ('answered', 'auto_answered')
            ORDER BY created_at ASC, id ASC
            """,
            job_id,
        )
        return [dict(r) for r in rows]

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
            ORDER BY created_at ASC, id ASC
            """,
            issue_id,
        )
        return [dict(r) for r in rows]
