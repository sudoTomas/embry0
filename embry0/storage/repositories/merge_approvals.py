"""merge_approvals — one row per "merge this PR?" asked in the approval chat (#319 phase 2).

Every state change is a single conditional UPDATE, so two replies racing for
the same ask cannot both act: the one whose UPDATE matched owns the decision,
and the other sees the row as it now stands.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from embry0.storage.database import DatabasePool

_COLUMNS = (
    "id, repo, pr_number, pr_url, head_sha, job_id, case_id, status, dry_run, decided_by, "
    "decided_at, chat_id, detail, merge_sha, ask, posted_at, created_at, updated_at"
)

#: A decision whose merge call never reported back (the process died between
#: the claim and the outcome) may be claimed again after this long.
IN_FLIGHT_TIMEOUT = "10 minutes"


def _row(r: Any) -> dict[str, Any] | None:
    # The pool's JSONB codec decodes `ask`; no json.loads here. A tolerant
    # decode used to sit here and hid that every row was double-encoded.
    return dict(r) if r is not None else None


def _jsonable(value: dict[str, Any]) -> dict[str, Any]:
    """A plain-JSON copy (dates become strings) for the pool's JSONB codec,
    which encodes it once. Passing ``json.dumps(...)`` instead stores a JSON
    *string* and breaks every ``ask->>`` query (see storage/database.py)."""
    result: dict[str, Any] = json.loads(json.dumps(value, default=str))
    return result


class MergeApprovalsRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def create_pending(
        self,
        *,
        repo: str,
        pr_number: int,
        pr_url: str,
        head_sha: str,
        job_id: str | None,
        case_id: str | None,
        ask: dict[str, Any],
    ) -> dict[str, Any] | None:
        """The new ask, or None when the PR already has a pending one."""
        row = await self._db.fetchrow(
            f"""
            INSERT INTO merge_approvals (id, repo, pr_number, pr_url, head_sha, job_id, case_id, ask)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
            ON CONFLICT (repo, pr_number) WHERE status = 'pending' DO NOTHING
            RETURNING {_COLUMNS}
            """,
            f"mrg-{uuid.uuid4().hex[:12]}",
            repo,
            pr_number,
            pr_url,
            head_sha,
            job_id,
            case_id,
            _jsonable(ask),
        )
        return _row(row)

    async def get(self, approval_id: str) -> dict[str, Any] | None:
        return _row(await self._db.fetchrow(f"SELECT {_COLUMNS} FROM merge_approvals WHERE id = $1", approval_id))

    async def pending_for_pr(self, repo: str, pr_number: int) -> dict[str, Any] | None:
        return _row(
            await self._db.fetchrow(
                f"SELECT {_COLUMNS} FROM merge_approvals WHERE repo = $1 AND pr_number = $2 AND status = 'pending'",
                repo,
                pr_number,
            )
        )

    async def list_recent(
        self, *, status: str | None = None, limit: int = 50, repos: Sequence[str] | None = None
    ) -> list[dict[str, Any]]:
        """The newest asks; ``repos`` (lower-cased slugs) keeps those of these repositories, before the limit."""
        rows = await self._db.fetch(
            f"SELECT {_COLUMNS} FROM merge_approvals WHERE ($1::text IS NULL OR status = $1) "
            "AND ($3::text[] IS NULL OR lower(repo) = ANY($3::text[])) "
            "ORDER BY created_at DESC LIMIT $2",
            status,
            limit,
            None if repos is None else list(repos),
        )
        return [r for r in (_row(x) for x in rows) if r is not None]

    async def unposted_pending(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Asks recorded but not yet shown in the chat, oldest first."""
        rows = await self._db.fetch(
            f"SELECT {_COLUMNS} FROM merge_approvals WHERE status = 'pending' AND posted_at IS NULL "
            "ORDER BY created_at LIMIT $1",
            limit,
        )
        return [r for r in (_row(x) for x in rows) if r is not None]

    async def posted_pending_due(self, *, recheck: timedelta, limit: int = 50) -> list[dict[str, Any]]:
        """Posted, still-open asks not looked at for ``recheck``: the ones
        the sweep may retire. ``touch`` marks a look."""
        rows = await self._db.fetch(
            f"SELECT {_COLUMNS} FROM merge_approvals WHERE status = 'pending' AND posted_at IS NOT NULL "
            "AND updated_at < NOW() - $1::interval ORDER BY updated_at LIMIT $2",
            recheck,
            limit,
        )
        return [r for r in (_row(x) for x in rows) if r is not None]

    async def mark_posted(self, approval_id: str, *, ask: dict[str, Any]) -> dict[str, Any] | None:
        """Record that the ask went out, with what it said. Only once."""
        row = await self._db.fetchrow(
            f"""
            UPDATE merge_approvals SET posted_at = NOW(), ask = $2::jsonb, updated_at = NOW()
             WHERE id = $1 AND status = 'pending' AND posted_at IS NULL
            RETURNING {_COLUMNS}
            """,
            approval_id,
            _jsonable(ask),
        )
        return _row(row)

    async def touch(self, approval_id: str) -> None:
        await self._db.execute(
            "UPDATE merge_approvals SET updated_at = NOW() WHERE id = $1 AND status = 'pending'", approval_id
        )

    async def retire(self, approval_id: str, *, status: str, detail: str) -> dict[str, Any] | None:
        """End an unanswered ask without a decision: its PR was merged or
        closed elsewhere (``closed``) or nobody answered in time (``expired``)."""
        if status not in ("closed", "expired"):
            raise ValueError(f"not a retirement: {status!r}")
        row = await self._db.fetchrow(
            f"""
            UPDATE merge_approvals SET status = $2, detail = $3, updated_at = NOW()
             WHERE id = $1 AND status = 'pending'
            RETURNING {_COLUMNS}
            """,
            approval_id,
            status,
            detail,
        )
        return _row(row)

    async def claim_approval(
        self, approval_id: str, *, decided_by: str, chat_id: str, dry_run: bool
    ) -> dict[str, Any] | None:
        """Take the ask for an approve decision; None when someone else holds it
        or it is already final. ``failed`` may be approved again; a real
        (non-dry-run) claim that never finished is reclaimable after
        ``IN_FLIGHT_TIMEOUT``; and a dry-run "would merge" can be approved
        for real once the mode is ``on``."""
        row = await self._db.fetchrow(
            f"""
            UPDATE merge_approvals
               SET status = 'approved', dry_run = $4, decided_by = $2, chat_id = $3,
                   decided_at = NOW(), updated_at = NOW()
             WHERE id = $1
               AND (status IN ('pending', 'failed')
                    OR (status = 'approved' AND NOT dry_run
                        AND updated_at < NOW() - INTERVAL '{IN_FLIGHT_TIMEOUT}')
                    OR (status = 'approved' AND dry_run AND NOT $4))
            RETURNING {_COLUMNS}
            """,
            approval_id,
            decided_by,
            chat_id,
            dry_run,
        )
        return _row(row)

    async def reject(self, approval_id: str, *, decided_by: str, chat_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            f"""
            UPDATE merge_approvals
               SET status = 'rejected', decided_by = $2, chat_id = $3, decided_at = NOW(), updated_at = NOW()
             WHERE id = $1 AND status IN ('pending', 'failed')
            RETURNING {_COLUMNS}
            """,
            approval_id,
            decided_by,
            chat_id,
        )
        return _row(row)

    async def release(self, approval_id: str, *, detail: str) -> dict[str, Any] | None:
        """Hand a claimed ask back to ``pending`` with the refusal reason."""
        row = await self._db.fetchrow(
            f"""
            UPDATE merge_approvals SET status = 'pending', detail = $2, updated_at = NOW()
             WHERE id = $1 AND status = 'approved'
            RETURNING {_COLUMNS}
            """,
            approval_id,
            detail,
        )
        return _row(row)

    async def finish(
        self,
        approval_id: str,
        *,
        status: str,
        detail: str | None = None,
        merge_sha: str | None = None,
    ) -> dict[str, Any] | None:
        """Record the outcome of a claimed decision."""
        if status not in ("approved", "merged", "failed", "stale"):
            raise ValueError(f"not a decision outcome: {status!r}")
        row = await self._db.fetchrow(
            f"""
            UPDATE merge_approvals
               SET status = $2, detail = $3, merge_sha = COALESCE($4, merge_sha), updated_at = NOW()
             WHERE id = $1 AND status = 'approved'
            RETURNING {_COLUMNS}
            """,
            approval_id,
            status,
            detail,
            merge_sha,
        )
        return _row(row)

    async def mark_stale(self, approval_id: str, *, detail: str) -> dict[str, Any] | None:
        """Retire a pending or claimed ask whose PR head moved."""
        row = await self._db.fetchrow(
            f"""
            UPDATE merge_approvals SET status = 'stale', detail = $2, updated_at = NOW()
             WHERE id = $1 AND status IN ('pending', 'approved', 'failed')
            RETURNING {_COLUMNS}
            """,
            approval_id,
            detail,
        )
        return _row(row)
