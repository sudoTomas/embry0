"""Leads (spec 2026-10-25): what the finders saw, one row per lead, never deleted.

``upsert`` writes what a finder sees on a pass (a lead seen again keeps its
``first_seen`` and comes back open if it had gone); ``mark_gone`` closes the
leads of a repository and kind that a whole read no longer saw.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any

from embry0.services.leads import Lead
from embry0.storage.database import DatabasePool

_COLUMNS = (
    "id, kind, repo, source_key, title, summary, details, url, severity, effort, action, area, data, state,"
    " first_seen, last_seen, gone_at"
)


def _row(record: Any) -> dict[str, Any]:
    row = dict(record)
    # The pool decodes JSONB; a pool without the codec hands back its text.
    if isinstance(row.get("data"), str):
        row["data"] = json.loads(row["data"])
    return row


class LeadsRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def upsert(self, leads: Sequence[Lead], *, now: datetime, areas: dict[str, str | None] | None = None) -> None:
        """Write ``leads`` as seen at ``now``; ``areas`` maps a lead id to its area name."""
        for lead in leads:
            data = {**dict(lead.data), "paths": list(lead.paths), "labels": list(lead.labels)}
            await self._db.execute(
                """
                INSERT INTO leads (id, kind, repo, source_key, title, summary, details, url, severity, effort,
                                   action, area, data, state, first_seen, last_seen)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, 'open', $14, $14)
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title, summary = EXCLUDED.summary, details = EXCLUDED.details,
                    url = EXCLUDED.url, severity = EXCLUDED.severity, effort = EXCLUDED.effort,
                    action = EXCLUDED.action, area = EXCLUDED.area, data = EXCLUDED.data,
                    state = 'open', last_seen = EXCLUDED.last_seen, gone_at = NULL
                """,
                lead.id,
                lead.kind,
                lead.repo,
                lead.source_key,
                lead.title[:500],
                lead.summary[:1000],
                lead.details[:8000],
                lead.url,
                lead.severity,
                lead.effort,
                lead.action,
                (areas or {}).get(lead.id),
                data,
                now,
            )

    async def mark_gone(self, repo: str, kind: str, seen: Collection[str], *, now: datetime) -> int:
        """Close the open leads of ``repo`` and ``kind`` not in ``seen``; how many closed."""
        result = await self._db.execute(
            """
            UPDATE leads SET state = 'gone', gone_at = $4
            WHERE repo = $1 AND kind = $2 AND state = 'open' AND NOT (id = ANY($3::text[]))
            """,
            repo,
            kind,
            sorted(seen),
            now,
        )
        try:
            return int(str(result).rsplit(" ", 1)[-1])
        except ValueError:
            return 0

    async def known_ids(self, repo: str, kind: str) -> set[str]:
        """Every lead of ``repo`` and ``kind`` ever stored, open or gone."""
        rows = await self._db.fetch("SELECT id FROM leads WHERE repo = $1 AND kind = $2", repo, kind)
        return {str(r["id"]) for r in rows}

    async def open_sources(self, repo: str, kind: str) -> dict[str, str]:
        """The open leads of ``repo`` and ``kind``: id to what the finder knows it by."""
        rows = await self._db.fetch(
            "SELECT id, source_key FROM leads WHERE repo = $1 AND kind = $2 AND state = 'open'", repo, kind
        )
        return {str(r["id"]): str(r["source_key"]) for r in rows}

    async def new_since(self, repo: str, kind: str, since: datetime) -> int:
        """Leads of ``repo`` and ``kind`` first seen after ``since``: what a daily cap counts."""
        return int(
            await self._db.fetchval(
                "SELECT count(*) FROM leads WHERE repo = $1 AND kind = $2 AND first_seen > $3", repo, kind, since
            )
            or 0
        )

    async def open_leads(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch(f"SELECT {_COLUMNS} FROM leads WHERE state = 'open' ORDER BY first_seen DESC")
        return [_row(r) for r in rows]

    async def get(self, lead_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM leads WHERE id = $1", lead_id)
        return _row(row) if row else None

    async def list(
        self, *, repo: str | None = None, kind: str | None = None, state: str | None = "open", limit: int = 200
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        for column, value in (("lower(repo)", repo.lower() if repo else None), ("kind", kind), ("state", state)):
            if value is not None:
                args.append(value)
                clauses.append(f"{column} = ${len(args)}")
        args.append(limit)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self._db.fetch(
            f"SELECT {_COLUMNS} FROM leads {where} ORDER BY last_seen DESC LIMIT ${len(args)}", *args
        )
        return [_row(r) for r in rows]
