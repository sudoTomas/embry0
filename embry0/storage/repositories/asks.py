"""Asks (spec 2026-10-27): the messages embry0 wants people to get about decisions, and their codes."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from embry0.services.asks import LIVE, code_candidates
from embry0.storage.database import DatabasePool

#: The repository of the decision ``{id}`` names, read at the time from the row it comes from: a finished job's,
#: an issue's, a watcher case's, a lead's, or, for any quest on offer that was answered, its answer's. NULL for an
#: audit finding no one answered: its id is a digest, so only the open decisions name its repository.
_DECISION_REPO = """
LEFT JOIN LATERAL (
    SELECT COALESCE(
        (SELECT j.repo FROM jobs j WHERE {id} LIKE 'done:%' AND j.job_id = substr({id}, 6)),
        (SELECT i.repo FROM issues i WHERE {id} LIKE 'offer:issue:%' AND i.id = substr({id}, 13)),
        (SELECT c.repo FROM healing_cases c WHERE {id} LIKE 'offer:case:%' AND c.id = substr({id}, 12)),
        (SELECT l.repo FROM leads l WHERE {id} LIKE 'offer:%' AND l.id = substr({id}, 7)),
        (SELECT pd.repo FROM proposal_decisions pd WHERE {id} LIKE 'offer:%' AND pd.proposal_id = substr({id}, 7)
         ORDER BY pd.created_at DESC LIMIT 1)
    ) AS repo
) decision ON TRUE
"""

#: Passes at making the codes a batch lacks: each pass after the first follows another writer taking a code first.
_CODE_PASSES = 3


class AsksRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def code(self, decision_id: str) -> str:
        """The decision's code, made once and kept: never another decision's."""
        return (await self.make_codes([decision_id]))[decision_id]

    async def make_codes(self, decision_ids: Iterable[str]) -> dict[str, str]:
        """Each decision's code, made for those that have none: never another decision's.

        Made in the order given, so where two new decisions' letters collide
        the first gets the shorter code, as making them one by one does. A few
        statements however many decisions (#273): one per code length a batch
        needs, usually one (:meth:`_choose`), never a read of every code stored.
        """
        ids = list(dict.fromkeys(decision_ids))
        found = await self.codes(ids)
        for _ in range(_CODE_PASSES):
            missing = [d for d in ids if d not in found]
            if not missing:
                return found
            made = await self._choose(missing)
            # A code another writer took meanwhile is skipped here and made again on the next pass.
            await self._db.execute(
                "INSERT INTO decision_codes (decision_id, code) SELECT * FROM unnest($1::text[], $2::text[]) "
                "ON CONFLICT DO NOTHING",
                list(made),
                list(made.values()),
            )
            found = await self.codes(ids)
        missing = [d for d in ids if d not in found]
        if missing:
            raise RuntimeError(f"no code stored for {len(missing)} decision(s) after {_CODE_PASSES} passes")
        return found

    async def _choose(self, missing: Sequence[str]) -> dict[str, str]:
        """The code each of ``missing`` gets, in order: what :func:`code_for` gives it against every stored code
        and the codes of those before it, as making them one by one does.

        Asked one length at a time: every decision's four letters first, then
        five letters for those whose four were taken, and so on. A code only
        ever collides with one of its own length, so settling each length in
        the order given gives each decision the code ``code_for`` would. Each
        statement names one candidate per decision still waiting, so 20,000
        new decisions send 20,000 candidates, not every prefix of every id.
        """
        candidates = {d: code_candidates(d) for d in missing}
        made: dict[str, str] = {}
        waiting = list(missing)
        size = 0
        while waiting:
            if any(size >= len(candidates[d]) for d in waiting):
                raise ValueError("no free code")
            asked = sorted({candidates[d][size] for d in waiting})
            rows = await self._db.fetch("SELECT code FROM decision_codes WHERE code = ANY($1::text[])", asked)
            taken = {str(r["code"]) for r in rows}
            later: list[str] = []
            for decision_id in waiting:
                code = candidates[decision_id][size]
                if code in taken:
                    later.append(decision_id)
                else:
                    made[decision_id] = code
                    taken.add(code)  # the next decision of this batch may not take it too
            waiting = later
            size += 1
        return made

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

    async def queue(self, asks: Iterable[tuple[str, str, datetime]]) -> list[str]:
        """Queue an ask for each ``(decision id, recipient, due_at)`` its person holds no live ask about,
        each decision given its code first; the ids of the asks queued.

        A fixed number of statements however many asks, so a tick that queues
        the asks of every open decision at once makes no round trip per
        decision (#273).
        """
        rows = list(asks)
        if not rows:
            return []
        await self.make_codes(decision_id for decision_id, _, _ in rows)
        inserted = await self._db.fetch(
            """
            INSERT INTO asks (id, decision_id, recipient, due_at)
            SELECT * FROM unnest($1::text[], $2::text[], $3::text[], $4::timestamptz[])
            ON CONFLICT DO NOTHING RETURNING id
            """,
            [f"ask-{uuid.uuid4().hex[:12]}" for _ in rows],
            [decision_id for decision_id, _, _ in rows],
            [recipient.lower() for _, recipient, _ in rows],
            [due_at for _, _, due_at in rows],
        )
        return [str(r["id"]) for r in inserted]

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

    async def recent(
        self, *, limit: int = 100, repos: Iterable[str] | None = None, decision_ids: Iterable[str] = ()
    ) -> list[dict[str, Any]]:
        """The newest asks. With ``repos`` (lower-cased slugs), only the asks whose decision's repository is one of
        them (``decision_repo``, read at the time), or whose decision is in ``decision_ids``, before the limit."""
        if repos is None:
            rows = await self._db.fetch("SELECT * FROM asks ORDER BY created_at DESC LIMIT $1", limit)
            return [dict(r) for r in rows]
        rows = await self._db.fetch(
            "SELECT a.*, decision.repo AS decision_repo FROM asks a"
            + _DECISION_REPO.format(id="a.decision_id")
            + "WHERE LOWER(decision.repo) = ANY($2::text[]) OR a.decision_id = ANY($3::text[]) "
            "ORDER BY a.created_at DESC LIMIT $1",
            limit,
            list(repos),
            sorted(set(decision_ids)),
        )
        return [dict(r) for r in rows]

    async def decision_repos(self, decision_ids: Iterable[str]) -> dict[str, str]:
        """Each decision's repository, read at the time from the row it comes from; a decision whose repository
        cannot be read back, an audit finding no one answered, is left out."""
        ids = sorted(set(decision_ids))
        if not ids:
            return {}
        rows = await self._db.fetch(
            "SELECT d.id, decision.repo FROM unnest($1::text[]) AS d(id)" + _DECISION_REPO.format(id="d.id"), ids
        )
        return {str(r["id"]): str(r["repo"]) for r in rows if r["repo"] is not None}
