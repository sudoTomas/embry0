"""Job briefs (migration 119, spec 2026-10-26): one plain-words brief per job."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from embry0.services.job_briefs import LIVE_STATUSES, RECENT_DAYS, TASK_CHARS, JobBrief, input_hash
from embry0.storage.database import DatabasePool

# A job's brief inputs (embry0.services.job_briefs.job_inputs), its task cut as
# the brief reads it, and the hash of the brief stored for it.
_INPUTS = f"""
j.job_id, LEFT(j.task, {TASK_CHARS}) AS task, j.repo, j.area, j.job_kind, j.pipeline_template, j.work_nature,
i.title AS issue_title
"""

_CANDIDATES_SQL = f"""
SELECT {_INPUTS}, j.status, b.input_hash AS brief_hash
FROM jobs j
LEFT JOIN issues i ON i.id = j.issue_id
LEFT JOIN job_briefs b ON b.job_id = j.job_id
WHERE j.status = ANY($1::text[]) OR j.finished_at > NOW() - make_interval(days => $2)
ORDER BY (j.status = ANY($1::text[])) DESC, j.created_at DESC
LIMIT $3
"""

_CURRENT_SQL = f"""
SELECT {_INPUTS}, b.input_hash AS brief_hash, b.title, b.what, b.why, b.done, b.size, b.model, b.written_at
FROM job_briefs b
JOIN jobs j ON j.job_id = b.job_id
LEFT JOIN issues i ON i.id = j.issue_id
WHERE b.job_id = ANY($1::text[])
"""


class JobBriefsRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def candidates(self, limit: int = 200) -> list[dict[str, Any]]:
        """Live jobs, then the ones finished in the last week, newest first, each with its inputs and stored hash."""
        rows = await self._db.fetch(_CANDIDATES_SQL, list(LIVE_STATUSES), RECENT_DAYS, limit)
        return [dict(r) for r in rows]

    async def save(self, job_id: str, input_hash: str, brief: JobBrief, *, model: str) -> None:
        """Store the brief written from ``input_hash``, in place of any earlier one."""
        await self._db.execute(
            """
            INSERT INTO job_briefs (job_id, input_hash, title, what, why, done, size, model)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (job_id) DO UPDATE SET
                input_hash = EXCLUDED.input_hash, title = EXCLUDED.title, what = EXCLUDED.what,
                why = EXCLUDED.why, done = EXCLUDED.done, size = EXCLUDED.size, model = EXCLUDED.model,
                written_at = NOW()
            """,
            job_id,
            input_hash,
            brief.title,
            brief.what,
            brief.why,
            brief.done,
            brief.size,
            model,
        )

    async def current(self, job_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        """The stored briefs of ``job_ids`` that were written from the job as it is now, by job id."""
        ids = sorted({str(i) for i in job_ids if i})
        if not ids:
            return {}
        rows = [dict(r) for r in await self._db.fetch(_CURRENT_SQL, ids)]
        return {str(r["job_id"]): r for r in rows if r["brief_hash"] == input_hash(r)}
