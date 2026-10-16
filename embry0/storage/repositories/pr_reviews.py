"""Pull request reviews (#30): one row per review a trigger asked for.

``trigger_key`` is UNIQUE, so a redelivered webhook — before or after a
restart — claims nothing the second time. A row outlives its job (``job_id``
is SET NULL when the job is purged): the next review of the same pull request
reads the earlier verdict from here.
"""

from __future__ import annotations

import uuid
from typing import Any

from embry0.storage.database import DatabasePool

_COLUMNS = "id, repo, number, head_sha, trigger, trigger_key, job_id, verdict, created_at, posted_at"


class PrReviewsRepository:
    """CRUD for ``pr_reviews`` (migration 96)."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def exists(self, trigger_key: str) -> bool:
        return bool(await self._db.fetchval("SELECT 1 FROM pr_reviews WHERE trigger_key = $1", trigger_key))

    async def claim(self, *, repo: str, number: int, head_sha: str, trigger: str, trigger_key: str) -> str | None:
        """Record the review. Its id, or None when ``trigger_key`` was already claimed."""
        review_id = f"prr-{uuid.uuid4().hex[:12]}"
        row = await self._db.fetchrow(
            """
            INSERT INTO pr_reviews (id, repo, number, head_sha, trigger, trigger_key)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (trigger_key) DO NOTHING
            RETURNING id
            """,
            review_id,
            repo,
            number,
            head_sha,
            trigger,
            trigger_key,
        )
        return str(row["id"]) if row else None

    async def attach_job(self, review_id: str, job_id: str) -> None:
        await self._db.execute("UPDATE pr_reviews SET job_id = $2 WHERE id = $1", review_id, job_id)

    async def release(self, review_id: str) -> None:
        """Give a claim back when its job could not be created. A review with a job is kept."""
        await self._db.execute("DELETE FROM pr_reviews WHERE id = $1 AND job_id IS NULL", review_id)

    async def record_outcome(self, review_id: str, *, verdict: dict[str, Any] | None, posted: bool) -> None:
        await self._db.execute(
            "UPDATE pr_reviews SET verdict = $2::jsonb, "
            "posted_at = CASE WHEN $3::boolean THEN NOW() ELSE posted_at END WHERE id = $1",
            review_id,
            verdict,
            posted,
        )

    async def get(self, review_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM pr_reviews WHERE id = $1", review_id)
        return dict(row) if row else None

    async def previous_reviews(
        self, repo: str, number: int, *, exclude_job_id: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Posted reviews of this pull request, newest first, never ``exclude_job_id``'s own."""
        rows = await self._db.fetch(
            f"SELECT {_COLUMNS} FROM pr_reviews "
            "WHERE repo = $1 AND number = $2 AND posted_at IS NOT NULL AND job_id IS DISTINCT FROM $3 "
            "ORDER BY created_at DESC, id DESC LIMIT $4",
            repo,
            number,
            exclude_job_id,
            limit,
        )
        return [dict(r) for r in rows]
