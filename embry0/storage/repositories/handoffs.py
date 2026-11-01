"""Hand-offs (spec 2026-10-27): a decision one person sent another, who then decides it."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from embry0.storage.database import DatabasePool

#: The states a hand-off can end in; ``open`` is the only one still waiting.
CLOSED_STATES = ("decided", "returned", "passed", "cancelled")


class HandoffsRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def send(
        self, *, decision_id: str, repo: str, title: str, from_actor: str, to_email: str, note: str, now: datetime
    ) -> dict[str, Any]:
        """Open a hand-off; one already open for the decision is closed as ``passed`` first."""
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE handoffs SET state = 'passed', closed_at = $2, closed_by = $3 "
                "WHERE decision_id = $1 AND state = 'open'",
                decision_id,
                now,
                from_actor,
            )
            row = await conn.fetchrow(
                """
                INSERT INTO handoffs (id, decision_id, repo, title, from_actor, to_email, note, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING *
                """,
                f"ho-{uuid.uuid4().hex[:12]}",
                decision_id,
                repo,
                title[:300],
                from_actor,
                to_email.lower(),
                note[:2000],
                now,
            )
        return dict(row)

    async def get(self, handoff_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow("SELECT * FROM handoffs WHERE id = $1", handoff_id)
        return dict(row) if row else None

    async def open_for(self, decision_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        """The open hand-off of each of ``decision_ids`` that has one."""
        ids = sorted(set(decision_ids))
        if not ids:
            return {}
        rows = await self._db.fetch(
            "SELECT * FROM handoffs WHERE state = 'open' AND decision_id = ANY($1::text[])", ids
        )
        return {str(r["decision_id"]): dict(r) for r in rows}

    async def open_to(self, email: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT * FROM handoffs WHERE state = 'open' AND LOWER(to_email) = LOWER($1) ORDER BY created_at DESC",
            email,
        )
        return [dict(r) for r in rows]

    async def recent(self, *, limit: int = 100, repos: Sequence[str] | None = None) -> list[dict[str, Any]]:
        """The newest hand-offs; ``repos`` (lower-cased slugs) narrows them to those repositories."""
        if repos is None:
            rows = await self._db.fetch("SELECT * FROM handoffs ORDER BY created_at DESC LIMIT $1", limit)
        else:
            rows = await self._db.fetch(
                "SELECT * FROM handoffs WHERE LOWER(repo) = ANY($2::text[]) ORDER BY created_at DESC LIMIT $1",
                limit,
                list(repos),
            )
        return [dict(r) for r in rows]

    async def close(
        self,
        *,
        decision_id: str | None = None,
        handoff_id: str | None = None,
        state: str,
        outcome: str | None,
        by: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        """Close the open hand-off of a decision (or the one by id); None when none was open."""
        if state not in CLOSED_STATES:
            raise ValueError(f"a hand-off closes as one of {', '.join(CLOSED_STATES)}")
        row = await self._db.fetchrow(
            """
            UPDATE handoffs SET state = $3, outcome = $4, closed_by = $5, closed_at = $6
            WHERE state = 'open' AND (decision_id = $1 OR id = $2) RETURNING *
            """,
            decision_id or "",
            handoff_id or "",
            state,
            outcome,
            by,
            now,
        )
        return dict(row) if row else None
