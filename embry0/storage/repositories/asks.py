"""Asks (spec 2026-10-27): the messages embry0 wants people to get about decisions, and their codes."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from embry0.services.asks import LIVE, code_for
from embry0.storage.database import DatabasePool


class AsksRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def code(self, decision_id: str) -> str:
        """The decision's code, made once and kept: never another decision's."""
        row = await self._db.fetchrow("SELECT code FROM decision_codes WHERE decision_id = $1", decision_id)
        if row is not None:
            return str(row["code"])
        taken = {str(r["code"]) for r in await self._db.fetch("SELECT code FROM decision_codes")}
        code = code_for(decision_id, taken)
        await self._db.execute(
            "INSERT INTO decision_codes (decision_id, code) VALUES ($1, $2) ON CONFLICT (decision_id) DO NOTHING",
            decision_id,
            code,
        )
        row = await self._db.fetchrow("SELECT code FROM decision_codes WHERE decision_id = $1", decision_id)
        return str(row["code"]) if row is not None else code

    async def decision_of(self, code: str) -> str | None:
        row = await self._db.fetchrow("SELECT decision_id FROM decision_codes WHERE code = UPPER($1)", code)
        return str(row["decision_id"]) if row is not None else None

    async def codes(self, decision_ids: Iterable[str]) -> dict[str, str]:
        ids = sorted(set(decision_ids))
        if not ids:
            return {}
        rows = await self._db.fetch(
            "SELECT decision_id, code FROM decision_codes WHERE decision_id = ANY($1::text[])", ids
        )
        return {str(r["decision_id"]): str(r["code"]) for r in rows}

    async def live(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch("SELECT * FROM asks WHERE state = ANY($1::text[]) ORDER BY due_at", list(LIVE))
        return [dict(r) for r in rows]

    async def add(self, *, decision_id: str, recipient: str, due_at: datetime) -> str | None:
        row = await self._db.fetchrow(
            """
            INSERT INTO asks (id, decision_id, recipient, due_at) VALUES ($1, $2, $3, $4)
            ON CONFLICT DO NOTHING RETURNING id
            """,
            f"ask-{uuid.uuid4().hex[:12]}",
            decision_id,
            recipient.lower(),
            due_at,
        )
        return str(row["id"]) if row is not None else None

    async def get(self, ask_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow("SELECT * FROM asks WHERE id = $1", ask_id)
        return dict(row) if row else None

    async def close(self, ask_ids: Iterable[str], *, state: str, now: datetime) -> int:
        """Close live asks as ``withdrawn`` or ``expired``; how many."""
        ids = sorted(set(ask_ids))
        if not ids:
            return 0
        result = await self._db.execute(
            "UPDATE asks SET state = $2, closed_at = $3 WHERE id = ANY($1::text[]) AND state = ANY($4::text[])",
            ids,
            state,
            now,
            list(LIVE),
        )
        try:
            return int(str(result).rsplit(" ", 1)[-1])
        except ValueError:
            return 0

    async def sent(self, ask_id: str, *, message_ref: str, now: datetime) -> dict[str, Any] | None:
        """A first send moves queued to sent; a reminder's send counts as one."""
        row = await self._db.fetchrow(
            """
            UPDATE asks SET
                state = CASE WHEN state = 'queued' THEN 'sent' ELSE 'reminded' END,
                sent_at = CASE WHEN state = 'queued' THEN $3 ELSE sent_at END,
                reminders = reminders + CASE WHEN state = 'queued' THEN 0 ELSE 1 END,
                message_ref = COALESCE(NULLIF($2, ''), message_ref)
            WHERE id = $1 AND state IN ('queued', 'sent') RETURNING *
            """,
            ask_id,
            message_ref,
            now,
        )
        return dict(row) if row else None

    async def answered(self, decision_id: str, *, answer: str, by: str, now: datetime) -> list[dict[str, Any]]:
        """Every live ask of the decision, answered: what they were, so the chat updates each message."""
        rows = await self._db.fetch(
            """
            UPDATE asks SET state = 'answered', answer = $2, answered_by = $3, answered_at = $4, closed_at = $4
            WHERE decision_id = $1 AND state = ANY($5::text[]) RETURNING *
            """,
            decision_id,
            answer,
            by,
            now,
            list(LIVE),
        )
        return [dict(r) for r in rows]

    async def last_answer(self, decision_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            "SELECT * FROM asks WHERE decision_id = $1 AND state = 'answered' ORDER BY answered_at DESC LIMIT 1",
            decision_id,
        )
        return dict(row) if row else None

    async def expired_pairs(self) -> dict[tuple[str, str], datetime | None]:
        """Each (decision, person) pair an expired ask keeps from being asked again, with until when.

        ``None``: an ask that reached its person and expired keeps them from
        being asked about that decision for good. A time: an ask the feed
        handed out that was never reported sent expired then (#295), and keeps
        them from being asked again until ``expire_days`` after it; the ask
        loop reads the organization's ``expire_days``. An ask that expired
        without being handed out or sent, which a release before #295 did to
        an ask that waited in the queue, keeps no one from being asked.
        """
        rows = await self._db.fetch(
            """
            SELECT decision_id, LOWER(recipient) AS r, bool_or(sent_at IS NOT NULL) AS sent,
                   max(closed_at) FILTER (WHERE sent_at IS NULL) AS cooling_since
            FROM asks
            WHERE state = 'expired' AND (sent_at IS NOT NULL OR handed_out_at IS NOT NULL)
            GROUP BY decision_id, LOWER(recipient)
            """
        )
        return {(str(r["decision_id"]), str(r["r"])): None if r["sent"] else r["cooling_since"] for r in rows}

    async def hand_out(self, ask_ids: Iterable[str], *, now: datetime) -> dict[str, datetime]:
        """Record that the feed handed these queued asks out: ``now`` the first time, kept after that.

        One statement stamps and answers, so two polls that hand the same ask
        out at once keep the same, earlier time. The ids of the asks still
        queued, each with the time it was first handed out.
        """
        ids = sorted(set(ask_ids))
        if not ids:
            return {}
        rows = await self._db.fetch(
            "UPDATE asks SET handed_out_at = COALESCE(handed_out_at, $2) "
            "WHERE id = ANY($1::text[]) AND state = 'queued' RETURNING id, handed_out_at",
            ids,
            now,
        )
        return {str(r["id"]): r["handed_out_at"] for r in rows}

    async def recent(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._db.fetch("SELECT * FROM asks ORDER BY created_at DESC LIMIT $1", limit)
        return [dict(r) for r in rows]
