"""The events progression reads (spec 2026-10-24): one row per thing that earns experience.

Read-only, from tables other features already write: nothing here stores
experience, so the same rows always read as the same levels. Each row is
``(source, agent_type, at, report)``: ``source`` names the event kind the
progression service prices, ``agent_type`` the agent that earned it (None
for the instance's own skills), ``at`` when it happened, and ``report`` the
job's report where the price depends on it (a context audit's outdated
claims), else None.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from embry0.orchestration import dispatch_actors
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.gate_refusal import gate_refusal_sql

_EVENTS_SQL = f"""
-- an agent run that ended without an error, for every agent but the three
-- whose experience comes from the job's outcome below
SELECT 'run' AS source, t.agent_type, t.created_at AS at, NULL::text AS report
FROM traces t
WHERE t.error_code IS NULL AND t.agent_type IS NOT NULL
  AND t.agent_type NOT IN ('developer', 'qa', 'context-auditor')
UNION ALL
-- the developer: a pull request a job opened was merged
SELECT 'merged', 'developer', COALESCE(j.delivery_state_at, j.finished_at, j.updated_at), NULL
FROM jobs j WHERE j.status = 'pr_merged'
UNION ALL
-- QA: a QA job that completed
SELECT 'qa_passed', 'qa', COALESCE(j.finished_at, j.updated_at), NULL
FROM jobs j WHERE j.pipeline_template = 'qa' AND j.status = 'completed'
UNION ALL
-- the context auditor: an audit that completed, with its report
SELECT 'audited', 'context-auditor', COALESCE(j.finished_at, j.updated_at), j.result_summary
FROM jobs j WHERE j.pipeline_template = 'context-audit' AND j.status = 'completed'
UNION ALL
-- the watcher: a healing case judged healed
SELECT 'healed', NULL, e.occurred_at, NULL
FROM healing_case_events e
WHERE e.event = 'verdict_recorded' AND e.detail -> 'updates' ->> 'healed_verdict' = 'healed'
UNION ALL
-- the watcher: a run that read logs
SELECT 'watched', NULL, w.created_at, NULL
FROM watcher_runs w WHERE w.log_lines > 0
UNION ALL
-- the schedules: a job a schedule started that the budget gate let run
SELECT 'scheduled', NULL, j.created_at, NULL
FROM jobs j WHERE j.dispatched_by = $1 AND NOT {gate_refusal_sql("j")}
ORDER BY at
"""


@dataclass(frozen=True)
class ProgressEvent:
    source: str
    agent_type: str | None
    at: datetime
    report: str | None = None


class ProgressionRepository:
    """The rows progression is computed from, oldest first."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def events(self) -> list[ProgressEvent]:
        rows: list[Any] = await self._db.fetch(_EVENTS_SQL, dispatch_actors.CONTEXT_AUDIT)
        return [
            ProgressEvent(str(r["source"]), r["agent_type"], r["at"], r["report"]) for r in rows if r["at"] is not None
        ]

    async def agent_types(self) -> list[str]:
        """Every agent the instance defines, built-in and custom."""
        return [str(r["type"]) for r in await self._db.fetch("SELECT type FROM agent_definitions ORDER BY type")]
