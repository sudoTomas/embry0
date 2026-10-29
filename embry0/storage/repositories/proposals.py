"""The rows owner proposals are read from, and the answers owners give (spec 2026-10-24).

Read-only over the cases, issues, jobs and preferences other features
write; ``proposal_decisions`` is the one table this feature owns.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from embry0.services.proposals import Decision
from embry0.storage.database import DatabasePool
from embry0.storage.schemas import FINISHED_WELL_STATUSES, JobStatus

_CASES_SQL = """
SELECT c.id, c.repo, c.title, c.severity, c.signature, c.occurrence_count, c.tracker_url,
       c.detected_at, c.tracked_at, c.created_at, c.work_nature
FROM healing_cases c
WHERE c.origin = 'watcher' AND c.stage = 'tracked'
  AND (c.tracker_key IS NOT NULL OR c.issue_id IS NOT NULL)
  AND EXISTS (SELECT 1 FROM watcher_stacks s WHERE s.repo = c.repo AND s.enabled)
ORDER BY c.created_at DESC
"""

_ISSUES_SQL = """
SELECT i.id, i.repo, i.title, i.body, i.labels, i.tracker_url, i.created_at
FROM issues i
WHERE i.status = 'open' AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.issue_id = i.id)
ORDER BY i.created_at DESC
"""

# The audit whose findings each repository offers, with its report: its newest context audit
# that finished its work ($1, FINISHED_WELL_STATUSES), unless that audit's pull request merged
# ($2). The merge may have fixed any of its findings, and any older audit's, so the repository
# then offers none until its next audit finishes: the newest is picked first and only then left
# out, so an older audit never stands in for it (#217). A pull request closed unmerged fixed
# nothing, and its audit's findings stand. A newer audit that failed or has not finished is
# passed over.
_AUDITS_SQL = """
SELECT newest.repo, newest.job_id, newest.finished_at, newest.result_summary
FROM (
    SELECT DISTINCT ON (j.repo) j.repo, j.job_id, j.status,
           COALESCE(j.finished_at, j.updated_at) AS finished_at, j.result_summary
    FROM jobs j
    WHERE j.pipeline_template = 'context-audit' AND j.status = ANY($1::text[]) AND j.repo IS NOT NULL
    ORDER BY j.repo, COALESCE(j.finished_at, j.updated_at) DESC
) newest
WHERE newest.status <> $2
"""


class ProposalsRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def cases(self) -> list[dict[str, Any]]:
        """Watcher cases waiting for someone to accept them, on stacks this embry0 watches."""
        return [dict(r) for r in await self._db.fetch(_CASES_SQL)]

    async def issues(self) -> list[dict[str, Any]]:
        """Open issues no job has started."""
        return [dict(r) for r in await self._db.fetch(_ISSUES_SQL)]

    async def audits(self) -> list[dict[str, Any]]:
        """The context audit whose findings each repository offers, and its report.

        Its newest audit that finished its work; a repository whose newest such audit's pull
        request merged is absent until its next audit finishes.
        """
        rows = await self._db.fetch(_AUDITS_SQL, list(FINISHED_WELL_STATUSES), JobStatus.PR_MERGED.value)
        return [dict(r) for r in rows]

    async def owners(self) -> dict[str, list[str]]:
        """The repositories whose preferences name owners, lower-cased name to logins."""
        rows = await self._db.fetch("SELECT repo, owners FROM repo_preferences WHERE owners IS NOT NULL")
        return {str(r["repo"]).lower(): [str(o) for o in r["owners"] or []] for r in rows}

    async def decisions(self) -> dict[str, Decision]:
        """The newest answer to each proposal."""
        rows = await self._db.fetch(
            """
            SELECT DISTINCT ON (proposal_id) proposal_id, decision, decided_by, reason, until, job_id, created_at
            FROM proposal_decisions
            ORDER BY proposal_id, created_at DESC, id DESC
            """
        )
        return {
            str(r["proposal_id"]): Decision(
                proposal_id=str(r["proposal_id"]),
                decision=str(r["decision"]),
                decided_by=str(r["decided_by"]),
                created_at=r["created_at"],
                until=r["until"],
                reason=str(r["reason"] or ""),
                job_id=r["job_id"],
            )
            for r in rows
        }

    async def record(
        self,
        *,
        proposal_id: str,
        repo: str,
        kind: str,
        decision: str,
        decided_by: str,
        reason: str = "",
        until: datetime | None = None,
        job_id: str | None = None,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO proposal_decisions (proposal_id, repo, kind, decision, decided_by, reason, until, job_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            proposal_id,
            repo,
            kind,
            decision,
            decided_by,
            reason,
            until,
            job_id,
        )
