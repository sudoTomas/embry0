"""Audit log read/retention repository (RAV-1056 Phase C).

The write path stays in ``embry0.audit`` (fire-and-forget, errors swallowed);
this repository is the read side: the global audit API's filtered listing and
the retention sweeper's pruning. ``audit_log`` deliberately has no FKs —
audit rows outlive every entity they reference.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from embry0.storage.database import DatabasePool


def _where(
    actors: Sequence[str] | None,
    action: str | None,
    since: datetime | None,
    until: datetime | None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    args: list[Any] = []
    if actors is not None:
        args.append(list(actors))
        clauses.append(f"actor = ANY(${len(args)})")
    if action is not None:
        args.append(action)
        clauses.append(f"action = ${len(args)}")
    if since is not None:
        args.append(since)
        clauses.append(f"created_at >= ${len(args)}")
    if until is not None:
        args.append(until)
        clauses.append(f"created_at < ${len(args)}")
    if not clauses:
        return "", args
    return " WHERE " + " AND ".join(clauses), args


class AuditRepository:
    def __init__(self, db: DatabasePool):
        self._db = db

    async def list_entries(
        self,
        *,
        actors: Sequence[str] | None = None,
        action: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Newest-first page of audit rows; ``actors=None`` = unrestricted.

        Explicit column list — trace_id is ops-internal and never leaves the
        repository. ``id DESC`` tiebreak keeps same-timestamp rows stable
        across pages.
        """
        where, args = _where(actors, action, since, until)
        args.extend((limit, offset))
        rows = await self._db.fetch(
            f"""
            SELECT id, action, actor, details, issue_id, created_at
            FROM audit_log{where}
            ORDER BY created_at DESC, id DESC
            LIMIT ${len(args) - 1} OFFSET ${len(args)}
            """,
            *args,
        )
        return [dict(r) for r in rows]

    async def count_entries(
        self,
        *,
        actors: Sequence[str] | None = None,
        action: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        where, args = _where(actors, action, since, until)
        row = await self._db.fetchrow(f"SELECT COUNT(*) AS n FROM audit_log{where}", *args)
        return int(row["n"]) if row else 0

    async def latest_entry(self, action: str, *, detail_key: str, detail_value: str) -> dict[str, Any] | None:
        """The newest ``action`` row whose ``details[detail_key]`` equals ``detail_value``, or None.

        idx_audit_log_action covers (action, created_at DESC), the filter and the
        order. Same explicit column list as ``list_entries``.
        """
        row = await self._db.fetchrow(
            """
            SELECT id, action, actor, details, issue_id, created_at
            FROM audit_log
            WHERE action = $1 AND details ->> $2::text = $3
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            action,
            detail_key,
            detail_value,
        )
        return dict(row) if row else None

    async def delete_older_than(self, cutoff: datetime) -> int:
        result = await self._db.execute("DELETE FROM audit_log WHERE created_at < $1", cutoff)
        # result is a command tag like "DELETE 5"
        try:
            return int(str(result).split()[-1])
        except (ValueError, IndexError):
            return 0
