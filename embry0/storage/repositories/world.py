"""The rows the world is drawn from (spec 2026-10-25, the world).

Read-only over jobs, their event log, owner proposal answers, repositories,
preferences and watched stacks: the world holds no state of its own.
"""

from __future__ import annotations

import json
from typing import Any

from embry0.storage.database import DatabasePool

#: Job statuses a party is still out on.
LIVE_STATUSES = ("pending", "running", "awaiting_input", "paused")

# A region: a repository with owners, a watched stack, or jobs in the last 90
# days, with how many jobs it had in that time.
_REGIONS_SQL = """
WITH active AS (
    SELECT repo, COUNT(*) AS jobs, MAX(created_at) AS last_job_at
    FROM jobs
    WHERE repo IS NOT NULL AND created_at > NOW() - INTERVAL '90 days'
    GROUP BY repo
)
SELECT r.repo,
       COALESCE(a.jobs, 0) AS jobs,
       a.last_job_at,
       COALESCE(CASE WHEN jsonb_typeof(p.owners) = 'array' THEN jsonb_array_length(p.owners) END, 0) > 0 AS owned,
       COALESCE(s.enabled, FALSE) AS watched
FROM (
    SELECT repo FROM active
    UNION SELECT repo FROM repo_preferences
          WHERE CASE WHEN jsonb_typeof(owners) = 'array' THEN jsonb_array_length(owners) ELSE 0 END > 0
    UNION SELECT repo FROM watcher_stacks WHERE enabled
) r
LEFT JOIN active a ON a.repo = r.repo
LEFT JOIN repo_preferences p ON p.repo = r.repo
LEFT JOIN watcher_stacks s ON s.repo = r.repo
ORDER BY r.repo
"""

_FRONTIER_SQL = """
SELECT repo FROM repos WHERE active ORDER BY repo LIMIT $1
"""

# The parties out now, and those back within the last ``$1`` seconds.
_JOBS_SQL = """
SELECT job_id, repo, task, status, pipeline_template, job_kind, created_at, started_at, finished_at,
       pr_url, delivery_state, area, issue_id, dispatched_by, current_stage, error_code, error_message,
       account_wait
FROM jobs
WHERE status = ANY($2::text[])
   OR COALESCE(finished_at, updated_at) > NOW() - make_interval(secs => $1)
ORDER BY created_at DESC
LIMIT $3
"""

_AGENT_EVENTS_SQL = """
SELECT job_id, event_type, text, created_at
FROM job_logs
WHERE job_id = ANY($1::text[]) AND event_type IN ('agent_started', 'agent_completed')
ORDER BY id
"""

_LEAD_LINKS_SQL = """
SELECT job_id, proposal_id
FROM proposal_decisions
WHERE decision = 'accept' AND job_id = ANY($1::text[])
"""

# The pull requests parties brought back in the last ``$1`` days.
_LOOT_SQL = """
SELECT job_id, repo, task, pr_url, delivery_state, delivery_state_at, finished_at, area, pipeline_template
FROM jobs
WHERE pr_url IS NOT NULL AND COALESCE(finished_at, created_at) > NOW() - make_interval(days => $1)
ORDER BY COALESCE(delivery_state_at, finished_at, created_at) DESC
LIMIT $2
"""


# Slop dungeon (spec 2026-10-25): the slop cleanups merged in the last ``$1``
# days, by repository and area — what each Slop Dungeon's ``cleaned`` counts.
_CLEANED_SQL = """
SELECT repo, area, COUNT(*) AS cleaned
FROM jobs
WHERE pipeline_template = 'slop-cleanup' AND repo IS NOT NULL
  AND (status = 'pr_merged' OR delivery_state = 'merged')
  AND COALESCE(delivery_state_at, finished_at, created_at) > NOW() - make_interval(days => $1)
GROUP BY repo, area
"""


_LAST_EVENT_SQL = """
SELECT job_id, MAX(created_at) AS at FROM job_logs WHERE job_id = ANY($1::text[]) GROUP BY job_id
"""

# One character's quests: every job it ran in, its share of the cost and time, and the player's evaluation.
_QUEST_LOG_SQL = """
SELECT j.job_id, j.repo, j.area, j.task, j.status, j.delivery_state, j.pr_url, j.pipeline_template,
       j.created_at, j.finished_at, j.error_code, j.dispatched_by,
       SUM(t.cost_usd) AS cost_usd, SUM(t.duration_ms) AS duration_ms, COUNT(*) AS runs,
       r.rating, r.verdict
FROM traces t
JOIN jobs j ON j.job_id = t.job_id
LEFT JOIN job_ratings r ON r.job_id = j.job_id
WHERE t.agent_type = $1
GROUP BY j.job_id, r.rating, r.verdict
ORDER BY j.created_at DESC
LIMIT $2
"""

# Each character's totals over all its quests (``$1`` names one, or NULL for all).
_STATS_SQL = """
WITH quests AS (
    SELECT t.agent_type, j.job_id, j.status, j.delivery_state,
           SUM(t.cost_usd) AS cost_usd, SUM(t.duration_ms) AS duration_ms, MAX(t.created_at) AS last_at
    FROM traces t JOIN jobs j ON j.job_id = t.job_id
    WHERE t.agent_type IS NOT NULL AND ($1::text IS NULL OR t.agent_type = $1)
    GROUP BY t.agent_type, j.job_id, j.status, j.delivery_state
)
SELECT q.agent_type,
       COUNT(*) AS quests,
       COUNT(*) FILTER (WHERE q.status IN ('completed', 'pr_merged') OR q.delivery_state = 'merged') AS succeeded,
       COUNT(*) FILTER (WHERE q.status IN ('failed', 'expired')) AS failed,
       COUNT(*) FILTER (WHERE q.delivery_state = 'merged') AS merged,
       AVG(q.cost_usd) AS avg_cost_usd,
       AVG(q.duration_ms) AS avg_duration_ms,
       MAX(q.last_at) AS last_quest_at,
       COUNT(r.job_id) FILTER (WHERE r.rating = 'good') AS rated_good,
       COUNT(r.job_id) FILTER (WHERE r.rating = 'needs_work') AS rated_needs_work,
       COUNT(r.job_id) FILTER (WHERE r.rating = 'bad') AS rated_bad
FROM quests q LEFT JOIN job_ratings r ON r.job_id = q.job_id
GROUP BY q.agent_type
"""

_QUEST_SQL = """
SELECT j.job_id, j.repo, j.area, j.task, j.status, j.delivery_state, j.delivery_state_at, j.pr_url,
       j.pipeline_template, j.job_kind, j.created_at, j.started_at, j.finished_at, j.error_code, j.error_message,
       j.total_cost_usd, j.result_summary, j.dispatched_by, j.issue_id, j.account_wait, j.work_nature,
       r.rating, r.verdict, r.note, r.followup_job_id, r.rated_by, r.rated_at
FROM jobs j LEFT JOIN job_ratings r ON r.job_id = j.job_id
WHERE j.job_id = $1
"""

_QUEST_RUNS_SQL = """
SELECT agent_type, model, cost_usd, duration_ms, result, error_code, created_at, tools_called, skill_actions, fallback
FROM traces WHERE job_id = $1 ORDER BY created_at
"""

# Finished quests a person sent (or that brought back a pull request) and nobody evaluated yet.
_INBOX_SQL = """
SELECT j.job_id, j.repo, j.area, j.task, j.status, j.delivery_state, j.pr_url, j.pipeline_template,
       j.created_at, j.finished_at, j.error_code, j.error_message, j.total_cost_usd, j.dispatched_by,
       -- drawn as the character that did the most of it: the costliest run but triage's
       (SELECT t.agent_type FROM traces t WHERE t.job_id = j.job_id AND t.agent_type <> 'triage'
        ORDER BY t.cost_usd DESC, t.created_at DESC LIMIT 1) AS agent_type
FROM jobs j
WHERE j.finished_at > NOW() - make_interval(days => $1)
  AND j.status NOT IN ('pending', 'running', 'awaiting_input', 'paused', 'cancelled')
  AND (j.pr_url IS NOT NULL OR j.dispatched_by IS NULL OR j.dispatched_by NOT LIKE 'embry0:%'
       OR j.dispatched_by = ANY($2::text[]))
  AND NOT EXISTS (SELECT 1 FROM job_ratings r WHERE r.job_id = j.job_id)
ORDER BY j.finished_at DESC
LIMIT $3
"""

_MERGE_APPROVAL_SQL = """
SELECT id, status, pr_url, pr_number FROM merge_approvals WHERE job_id = $1 ORDER BY created_at DESC LIMIT 1
"""

_RATE_SQL = """
INSERT INTO job_ratings (job_id, rating, verdict, note, followup_job_id, rated_by)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (job_id) DO UPDATE SET
    rating = COALESCE(EXCLUDED.rating, job_ratings.rating),
    verdict = COALESCE(EXCLUDED.verdict, job_ratings.verdict),
    note = CASE WHEN EXCLUDED.note = '' THEN job_ratings.note ELSE EXCLUDED.note END,
    followup_job_id = COALESCE(EXCLUDED.followup_job_id, job_ratings.followup_job_id),
    rated_by = EXCLUDED.rated_by,
    rated_at = NOW()
RETURNING job_id, rating, verdict, note, followup_job_id, rated_by, rated_at
"""


def _agent_of(text: str | None) -> str | None:
    """The agent an event names; None for a line that is not a JSON event."""
    try:
        event = json.loads(text or "")
    except (TypeError, ValueError):
        return None
    agent = event.get("agent") if isinstance(event, dict) else None
    return agent if isinstance(agent, str) and agent else None


class WorldRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def regions(self) -> list[dict[str, Any]]:
        return [dict(r) for r in await self._db.fetch(_REGIONS_SQL)]

    async def frontier(self, limit: int) -> list[str]:
        """Registered repositories, for the unexplored land at the map's edge."""
        return [str(r["repo"]) for r in await self._db.fetch(_FRONTIER_SQL, limit)]

    async def jobs(self, *, returning_seconds: float, limit: int = 60) -> list[dict[str, Any]]:
        """The jobs out now and those finished in the last ``returning_seconds``, newest first."""
        rows = await self._db.fetch(_JOBS_SQL, float(returning_seconds), list(LIVE_STATUSES), limit)
        return [dict(r) for r in rows]

    async def agent_runs(self, job_ids: list[str]) -> dict[str, list[tuple[str, str]]]:
        """Each job's agent starts and ends in order: ``(event, agent)`` pairs."""
        if not job_ids:
            return {}
        out: dict[str, list[tuple[str, str]]] = {}
        for row in await self._db.fetch(_AGENT_EVENTS_SQL, job_ids):
            agent = _agent_of(row["text"])
            if agent:
                out.setdefault(str(row["job_id"]), []).append((str(row["event_type"]), agent))
        return out

    async def lead_links(self, job_ids: list[str]) -> dict[str, str]:
        """The owner proposal each job was started from, by job id."""
        if not job_ids:
            return {}
        return {str(r["job_id"]): str(r["proposal_id"]) for r in await self._db.fetch(_LEAD_LINKS_SQL, job_ids)}

    async def loot(self, *, days: int, limit: int = 30) -> list[dict[str, Any]]:
        return [dict(r) for r in await self._db.fetch(_LOOT_SQL, days, limit)]

    async def cleaned(self, *, days: int) -> dict[tuple[str, str | None], int]:
        """The slop cleanups merged in the last ``days``, by (repository, area)."""
        rows = await self._db.fetch(_CLEANED_SQL, days)
        return {(str(r["repo"]), r["area"] or None): int(r["cleaned"] or 0) for r in rows}

    async def last_event_at(self, job_ids: list[str]) -> dict[str, Any]:
        """When each job last wrote to its event log: a party silent too long is stuck."""
        if not job_ids:
            return {}
        return {str(r["job_id"]): r["at"] for r in await self._db.fetch(_LAST_EVENT_SQL, job_ids)}

    async def quest_log(self, agent_type: str, *, limit: int = 50) -> list[dict[str, Any]]:
        return [dict(r) for r in await self._db.fetch(_QUEST_LOG_SQL, agent_type, limit)]

    async def stats(self, agent_type: str | None = None) -> dict[str, dict[str, Any]]:
        """Each character's quest totals, by agent type: one character's, or every one's."""
        return {str(r["agent_type"]): dict(r) for r in await self._db.fetch(_STATS_SQL, agent_type)}

    async def quest(self, job_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(_QUEST_SQL, job_id)
        return dict(row) if row else None

    async def quest_runs(self, job_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in await self._db.fetch(_QUEST_RUNS_SQL, job_id)]

    async def inbox(self, *, days: int, player_actors: list[str], limit: int = 30) -> list[dict[str, Any]]:
        return [dict(r) for r in await self._db.fetch(_INBOX_SQL, days, player_actors, limit)]

    async def merge_approval(self, job_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(_MERGE_APPROVAL_SQL, job_id)
        return dict(row) if row else None

    async def rate(
        self,
        job_id: str,
        *,
        rated_by: str,
        rating: str | None = None,
        verdict: str | None = None,
        note: str = "",
        followup_job_id: str | None = None,
    ) -> dict[str, Any]:
        """Record the person's evaluation; a field left out keeps what an earlier one said."""
        row = await self._db.fetchrow(_RATE_SQL, job_id, rating, verdict, note, followup_job_id, rated_by)
        return dict(row) if row else {}
