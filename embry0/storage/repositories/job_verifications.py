"""job_verifications — one durable verification result per job (#298, #299).

Written by the issue-to-PR ``verify`` node the moment the result exists (so a
job that fails or pauses afterwards still has it), read by the job-completion
handler to publish it on the PR, and by auto-merge (#310) through
``latest_for_head``. The ``report`` column is the full machine-readable
result (``VerificationResult.to_dict()``); the scalar columns are the parts
worth filtering on.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from embry0.storage.database import DatabasePool

_COLUMNS = (
    "job_id, repo, mode, tier, gate_verdict, blocking, browser_qa, human_merge_only, "
    "base_sha, head_sha, report, published_at, created_at, updated_at"
)


class JobVerificationsRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def upsert(self, *, job_id: str, repo: str, result: dict[str, Any]) -> None:
        """Store ``result`` for the job; a later run of the same job replaces
        the earlier one and clears ``published_at`` so it is published again."""
        gate = result.get("gate") or {}
        await self._db.execute(
            """
            INSERT INTO job_verifications (
                job_id, repo, mode, tier, gate_verdict, blocking, browser_qa,
                human_merge_only, base_sha, head_sha, report
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
            ON CONFLICT (job_id) DO UPDATE SET
                repo = EXCLUDED.repo,
                mode = EXCLUDED.mode,
                tier = EXCLUDED.tier,
                gate_verdict = EXCLUDED.gate_verdict,
                blocking = EXCLUDED.blocking,
                browser_qa = EXCLUDED.browser_qa,
                human_merge_only = EXCLUDED.human_merge_only,
                base_sha = EXCLUDED.base_sha,
                head_sha = EXCLUDED.head_sha,
                report = EXCLUDED.report,
                published_at = NULL,
                updated_at = NOW()
            """,
            job_id,
            repo,
            str(result.get("mode") or "on"),
            str((result.get("tier") or {}).get("tier") or ""),
            str(gate.get("verdict") or ""),
            bool(result.get("blocking")),
            str((result.get("browser_qa") or {}).get("planned") or ""),
            bool(result.get("human_merge_only")),
            str(gate.get("base_sha") or ""),
            str(gate.get("head_sha") or ""),
            result,
        )

    async def get(self, job_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM job_verifications WHERE job_id = $1", job_id)
        return dict(row) if row else None

    async def for_jobs(self, job_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Each job's result, as :meth:`get` reads it, in one statement; a job with none is left out."""
        if not job_ids:
            return {}
        rows = await self._db.fetch(
            f"SELECT {_COLUMNS} FROM job_verifications WHERE job_id = ANY($1::text[])", sorted(set(job_ids))
        )
        return {str(r["job_id"]): dict(r) for r in rows}

    async def latest_for_head(self, repo: str, head_sha: str) -> dict[str, Any] | None:
        """The newest result for exactly this commit of ``repo`` — a verdict
        for any other head says nothing about the PR as it stands."""
        if not head_sha:
            return None
        row = await self._db.fetchrow(
            f"SELECT {_COLUMNS} FROM job_verifications WHERE repo = $1 AND head_sha = $2 "
            "ORDER BY updated_at DESC LIMIT 1",
            repo,
            head_sha,
        )
        return dict(row) if row else None

    async def mark_published(self, job_id: str) -> None:
        await self._db.execute(
            "UPDATE job_verifications SET published_at = NOW(), updated_at = NOW() WHERE job_id = $1", job_id
        )
