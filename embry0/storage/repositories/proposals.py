"""The rows owner proposals are read from, and the answers owners give (spec 2026-10-24).

Read-only over the cases, issues, jobs and preferences other features
write; ``proposal_decisions`` is the one table this feature owns.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from embry0.services.context_audit import FIX_ACTOR
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
# that finished its work ($1, FINISHED_WELL_STATUSES), unless a merge may have fixed what it
# found. Two merges leave it out:
#   - its own pull request's ($2);
#   - another audit's, after it started (#236): it read the files before that merge, whether
#     the merged audit finished before it and merged while it ran, or started after it.
# Such a merge may have fixed any of its findings, and any older audit's, so the repository
# then offers none until an audit that started after the merge finishes: the newest is picked
# first and only then left out, so an older audit never stands in for it (#217). A merge is
# dated by the forge (delivery_state_at); without that date, by when its row last changed,
# which is no earlier than merge sync recording the merge, so an unknown date can only leave
# out more, never offer a claim the merge may have fixed. A pull request closed unmerged fixed
# nothing, and its audit's findings stand. A newer audit that failed or has not finished is
# passed over. A finding's fix job ($3, context_audit.FIX_ACTOR) is no audit: whatever became
# of it or its pull request, the audit before it is still the newest, and its other findings
# are still offered; the one the job fixes was answered when its owner accepted it (#222). Its
# merge leaves no audit out either: it changed that one claim.
_AUDITS_SQL = """
SELECT newest.repo, newest.job_id, newest.finished_at, newest.result_summary
FROM (
    SELECT DISTINCT ON (j.repo) j.repo, j.job_id, j.status, j.created_at,
           COALESCE(j.finished_at, j.updated_at) AS finished_at, j.result_summary
    FROM jobs j
    WHERE j.pipeline_template = 'context-audit' AND j.status = ANY($1::text[]) AND j.repo IS NOT NULL
      AND NOT (COALESCE(j.dispatched_by, '') = ANY($3::text[]))
    ORDER BY j.repo, COALESCE(j.finished_at, j.updated_at) DESC
) newest
WHERE newest.status <> $2
  AND NOT EXISTS (
    SELECT 1 FROM jobs merged
    WHERE merged.repo = newest.repo AND merged.pipeline_template = 'context-audit' AND merged.status = $2
      AND NOT (COALESCE(merged.dispatched_by, '') = ANY($3::text[]))
      AND COALESCE(merged.delivery_state_at, merged.updated_at) > newest.created_at
  )
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

        Its newest audit that finished its work, unless a merge may have fixed what it found: its
        own pull request's, or another audit's after it started. The repository is then absent
        until an audit that started after the merge finishes. A finding's fix job is no audit, and
        its merge does not count.
        """
        rows = await self._db.fetch(_AUDITS_SQL, list(FINISHED_WELL_STATUSES), JobStatus.PR_MERGED.value, [FIX_ACTOR])
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
