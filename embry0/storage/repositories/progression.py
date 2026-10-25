"""The events progression reads (spec 2026-10-24): one row per thing that earns experience.

Read-only, from tables other features already write: nothing here stores
experience, so the same rows always read as the same levels. Each row is
``(source, agent_type, at, report, tools_called, skill_actions)``:
``source`` names the event kind the progression service prices,
``agent_type`` the character that earned it (None for work no character
does, which counts toward the account's achievements only), ``at`` when it
happened, ``report`` the job's report where the price depends on it (a
context audit's outdated claims), and for a run the tool calls it made, by
name and by the skill they train.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from embry0.orchestration import dispatch_actors
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.gate_refusal import gate_refusal_sql

_EVENTS_SQL = f"""
-- an agent run that ended without an error, with the tool calls it made
SELECT 'run' AS source, t.agent_type, t.created_at AS at, NULL::text AS report, t.tools_called, t.skill_actions
FROM traces t
WHERE t.error_code IS NULL AND t.result = 'success' AND t.agent_type IS NOT NULL
UNION ALL
-- the developer: a pull request a job opened was merged
SELECT 'merged', 'developer', COALESCE(j.delivery_state_at, j.finished_at, j.updated_at), NULL, NULL, NULL
FROM jobs j WHERE j.status = 'pr_merged'
UNION ALL
-- the developer: a job that opened a pull request
SELECT 'opened', 'developer', COALESCE(j.finished_at, j.updated_at), NULL, NULL, NULL
FROM jobs j WHERE j.pr_url IS NOT NULL
UNION ALL
-- the developer: a merge on an issue whose earlier job had failed
SELECT 'comeback', 'developer', COALESCE(m.delivery_state_at, m.finished_at, m.updated_at), NULL, NULL, NULL
FROM jobs m
WHERE m.status = 'pr_merged' AND m.issue_id IS NOT NULL
  AND EXISTS (
    SELECT 1 FROM jobs f
    WHERE f.issue_id = m.issue_id AND f.status = 'failed' AND f.created_at < m.created_at
  )
UNION ALL
-- an interrupted job resumed to the end: the agent of its last run
SELECT 'resumed', (SELECT t.agent_type FROM traces t WHERE t.job_id = j.job_id ORDER BY t.created_at DESC LIMIT 1),
       COALESCE(j.finished_at, j.updated_at), NULL, NULL, NULL
FROM jobs j WHERE j.resume_attempts > 0 AND j.status IN ('completed', 'pr_merged')
UNION ALL
-- QA: a QA job that completed
SELECT 'qa_passed', 'qa', COALESCE(j.finished_at, j.updated_at), NULL, NULL, NULL
FROM jobs j WHERE j.pipeline_template = 'qa' AND j.status = 'completed'
UNION ALL
-- the context auditor: an audit that completed, with its report
SELECT 'audited', 'context-auditor', COALESCE(j.finished_at, j.updated_at), j.result_summary, NULL, NULL
FROM jobs j WHERE j.pipeline_template = 'context-audit' AND j.status = 'completed'
UNION ALL
-- a question an agent asked a person
SELECT 'asked', a.actor, a.created_at, NULL, NULL, NULL
FROM audit_log a WHERE a.action = 'issue.input_created'
UNION ALL
-- the watcher (no character): a healing case judged healed
SELECT 'healed', NULL, e.occurred_at, NULL, NULL, NULL
FROM healing_case_events e
WHERE e.event = 'verdict_recorded' AND e.detail -> 'updates' ->> 'healed_verdict' = 'healed'
UNION ALL
-- the watcher (no character): a run that read logs
SELECT 'watched', NULL, w.created_at, NULL, NULL, NULL
FROM watcher_runs w WHERE w.log_lines > 0
UNION ALL
-- the schedules (no character): a job a schedule started that the budget gate let run
SELECT 'scheduled', NULL, j.created_at, NULL, NULL, NULL
FROM jobs j WHERE j.dispatched_by = $1 AND NOT {gate_refusal_sql("j")}
ORDER BY at
"""


@dataclass(frozen=True)
class ProgressEvent:
    source: str
    agent_type: str | None
    at: datetime
    report: str | None = None
    #: A run's tool calls by name, and by action key (``kind:skill``); None for other sources.
    tools_called: dict[str, int] | None = None
    skill_actions: dict[str, int] | None = None


class ProgressionRepository:
    """The rows progression is computed from, oldest first."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def events(self) -> list[ProgressEvent]:
        rows: list[Any] = await self._db.fetch(_EVENTS_SQL, dispatch_actors.CONTEXT_AUDIT)
        return [
            ProgressEvent(
                str(r["source"]),
                r["agent_type"],
                r["at"],
                r["report"],
                r["tools_called"],
                r["skill_actions"],
            )
            for r in rows
            if r["at"] is not None
        ]

    async def journal(self) -> Journal:
        """Every job and every successful run, with its repo and issue: what quests and repo diaries read."""
        jobs: list[Any] = await self._db.fetch(_JOBS_SQL)
        runs: list[Any] = await self._db.fetch(_RUNS_SQL)
        return Journal(
            jobs=[
                JobRow(
                    str(r["repo"]),
                    r["issue_id"],
                    r["template"],
                    str(r["status"]),
                    r["created_at"],
                    r["at"],
                    r["report"],
                )
                for r in jobs
                if r["at"] is not None
            ],
            runs=[RunRow(str(r["agent_type"]), str(r["repo"]), r["issue_id"], r["at"]) for r in runs],
        )

    async def agent_types(self) -> list[str]:
        """Every agent the instance defines, built-in and custom."""
        return [str(r["type"]) for r in await self._db.fetch("SELECT type FROM agent_definitions ORDER BY type")]

    async def facts(self) -> list[Fact]:
        """Every step of every achievement metric, with its evidence, oldest first."""
        rows: list[Any] = await self._db.fetch(_FACTS_SQL)
        return [
            Fact(str(r["metric"]), r["at"], float(r["value"]), r["label"], r["url"], r["report"])
            for r in rows
            if r["at"] is not None
        ]


@dataclass(frozen=True)
class Fact:
    """One step of an achievement's metric: ``value`` added at ``at``, with what did it.

    ``label`` and ``url`` are the evidence the page shows (a pull request, a
    repo, an error's title); ``report`` is a context audit's report, which the
    service reads for its outdated claims.
    """

    metric: str
    at: datetime
    value: float = 1.0
    label: str | None = None
    url: str | None = None
    report: str | None = None


# Every metric is an outcome (spec 2026-10-25, the achievement diary). A merged
# pull request is one the forge confirmed merged, or whose job says so.
_FACTS_SQL = """
WITH merged AS (
    SELECT j.job_id, j.repo, j.issue_id, j.pr_url, j.pipeline_template, j.area, j.total_cost_usd,
           j.created_at, j.started_at, j.finished_at,
           COALESCE(j.delivery_state_at, j.finished_at, j.updated_at) AS at,
           COALESCE(j.repo, 'a job') || COALESCE(' #' || substring(j.pr_url from '([0-9]+)/?$'), '') AS label
    FROM jobs j
    WHERE j.delivery_state = 'merged' OR j.status = 'pr_merged'
),
healed AS (
    SELECT c.*, COALESCE(c.verified_at, c.verdict_checked_at, c.updated_at) AS healed_at
    FROM healing_cases c WHERE c.healed_verdict = 'healed'
)
-- shipping
SELECT 'merged' AS metric, m.at, 1::float8 AS value, m.label, m.pr_url AS url, NULL::text AS report FROM merged m
UNION ALL
SELECT 'first_try', m.at, 1, m.label, m.pr_url, NULL FROM merged m
WHERE NOT EXISTS (
    SELECT 1 FROM jobs e
    WHERE e.job_id <> m.job_id AND e.created_at < m.created_at
      AND ((m.issue_id IS NOT NULL AND e.issue_id = m.issue_id) OR (m.pr_url IS NOT NULL AND e.pr_url = m.pr_url))
)
UNION ALL
SELECT 'clean_kill', m.at, 1, m.label, m.pr_url, NULL FROM merged m
WHERE NOT EXISTS (
    SELECT 1 FROM traces t WHERE t.job_id = m.job_id AND (t.result <> 'success' OR t.error_code IS NOT NULL)
)
UNION ALL
SELECT 'bargain', m.at, 1, m.label, m.pr_url, NULL FROM merged m
WHERE m.total_cost_usd > 0 AND m.total_cost_usd < 1
UNION ALL
SELECT 'speedrun', m.at, 1, m.label, m.pr_url, NULL FROM merged m WHERE m.at - m.created_at < INTERVAL '1 day'
UNION ALL
SELECT 'world_hopper', f.at, 1, f.repo, f.pr_url, NULL
FROM (SELECT DISTINCT ON (repo) repo, at, pr_url FROM merged WHERE repo IS NOT NULL ORDER BY repo, at) f
UNION ALL
SELECT 'area_merged', m.at, 1, m.label || ' · ' || m.area, m.pr_url, NULL FROM merged m WHERE m.area IS NOT NULL
UNION ALL
SELECT 'hot_streak', max(m.at), 1, to_char(max(m.at) AT TIME ZONE 'UTC', 'YYYY-MM-DD'), NULL, NULL
FROM merged m GROUP BY date_trunc('day', m.at AT TIME ZONE 'UTC') HAVING count(*) >= 3
UNION ALL
SELECT 'full_party', m.at, 1, m.label, m.pr_url, NULL FROM merged m
WHERE EXISTS (SELECT 1 FROM traces t WHERE t.job_id = m.job_id AND t.agent_type = 'review' AND t.result = 'success')
  AND EXISTS (SELECT 1 FROM traces t WHERE t.job_id = m.job_id AND t.agent_type = 'qa' AND t.result = 'success')
-- slayer
UNION ALL
SELECT 'healed', h.healed_at, 1, h.title, h.tracker_url, NULL FROM healed h
UNION ALL
SELECT 'boss_healed', h.healed_at, 1, h.title, h.tracker_url, NULL FROM healed h WHERE h.severity = 'high'
UNION ALL
SELECT 'superior_healed', h.healed_at, 1, h.title, h.tracker_url, NULL FROM healed h WHERE h.occurrence_count >= 10
UNION ALL
SELECT 'never_respawned', h.healed_at, 1, h.title, h.tracker_url, NULL FROM healed h
WHERE NOT EXISTS (
    SELECT 1 FROM healing_case_events e WHERE e.case_id = h.id AND e.event = 'recurrence' AND e.occurred_at > h.healed_at
)
UNION ALL
SELECT 'safespotted', c.merged_at, 1, c.title, c.tracker_url, NULL FROM healing_cases c
WHERE c.origin = 'watcher' AND c.merged_at IS NOT NULL AND COALESCE(c.resolution_path, 'embry0') = 'embry0'
UNION ALL
SELECT 'cases_accepted', c.accepted_at, 1, c.title, c.tracker_url, NULL FROM healing_cases c
WHERE c.origin = 'watcher' AND c.accepted_at IS NOT NULL
UNION ALL
SELECT 'deployed_fix', c.deployed_at, 1, c.title, c.tracker_url, NULL FROM healing_cases c WHERE c.deployed_at IS NOT NULL
-- questing
UNION ALL
SELECT 'issue_done', f.at, 1, COALESCE(i.title, f.label), f.pr_url, NULL
FROM (SELECT DISTINCT ON (issue_id) * FROM merged WHERE issue_id IS NOT NULL ORDER BY issue_id, at) f
LEFT JOIN issues i ON i.id = f.issue_id
UNION ALL
SELECT 'side_quest', f.at, 1, COALESCE(i.title, f.label), f.pr_url, NULL
FROM (SELECT DISTINCT ON (issue_id) * FROM merged WHERE issue_id IS NOT NULL ORDER BY issue_id, at) f
JOIN issues i ON i.id = f.issue_id WHERE i.parent_issue_id IS NOT NULL
UNION ALL
SELECT 'owner_answer', d.created_at, 1, d.repo || ' · ' || d.decision, NULL, NULL FROM proposal_decisions d
UNION ALL
SELECT 'proposal_accepted', d.created_at, 1, d.repo, NULL, NULL FROM proposal_decisions d WHERE d.decision = 'accept'
UNION ALL
SELECT 'proposal_merged', m.at, 1, m.label, m.pr_url, NULL
FROM proposal_decisions d JOIN merged m ON m.job_id = d.job_id WHERE d.decision = 'accept'
UNION ALL
SELECT 'question_answered', q.answered_at, 1, left(q.question, 120), NULL, NULL
FROM issue_inputs q WHERE q.answered_at IS NOT NULL AND q.answer IS NOT NULL
-- lore
UNION ALL
SELECT 'audit', COALESCE(j.finished_at, j.updated_at), 1, j.repo, NULL, j.result_summary
FROM jobs j WHERE j.pipeline_template = 'context-audit' AND j.status = 'completed'
UNION ALL
SELECT 'audit_merged', m.at, 1, m.label, m.pr_url, NULL FROM merged m WHERE m.pipeline_template = 'context-audit'
UNION ALL
SELECT 'areas_mapped', f.at, 1, f.repo, f.pr_url, NULL
FROM (SELECT DISTINCT ON (repo) repo, at, pr_url FROM merged WHERE pipeline_template = 'map-areas' ORDER BY repo, at) f
UNION ALL
SELECT 'design_ninety', s.last_audit_at, 1, s.repo || ' · ' || s.kind || ' ' || s.score, NULL, NULL
FROM repo_standard_state s WHERE s.score >= 90 AND s.last_audit_at IS NOT NULL
-- guarding
UNION ALL
SELECT 'reviews', t.created_at, 1, COALESCE(j.repo, t.agent_type), j.pr_url, NULL
FROM traces t LEFT JOIN jobs j ON j.job_id = t.job_id
WHERE t.agent_type IN ('review', 'pr-reviewer') AND t.result = 'success' AND t.error_code IS NULL
UNION ALL
SELECT 'review_posted', r.posted_at, 1, r.repo || ' #' || r.number, NULL, NULL FROM pr_reviews r WHERE r.posted_at IS NOT NULL
UNION ALL
SELECT 'qa_passed', COALESCE(j.finished_at, j.updated_at), 1, j.repo, NULL, NULL
FROM jobs j WHERE j.pipeline_template = 'qa' AND j.status = 'completed'
UNION ALL
SELECT 'gate_blocked', COALESCE(j.finished_at, j.updated_at), 1, j.repo, j.pr_url, NULL
FROM jobs j WHERE j.error_code = 'ERR_VERIFY_NEW_TEST_FAILURES'
UNION ALL
SELECT 'setup_applied', COALESCE(p.decided_at, p.updated_at), 1, p.repo, NULL, NULL
FROM repo_setup_proposals p WHERE p.status = 'applied'
-- grit
UNION ALL
SELECT 'comeback', m.at, 1, m.label, m.pr_url, NULL FROM merged m
WHERE m.issue_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM jobs f WHERE f.issue_id = m.issue_id AND f.status = 'failed' AND f.created_at < m.created_at
)
UNION ALL
SELECT 'resumed', COALESCE(j.finished_at, j.updated_at), 1, j.repo, j.pr_url, NULL
FROM jobs j WHERE j.resume_attempts > 0 AND j.status IN ('completed', 'pr_merged')
UNION ALL
SELECT 'lag_survivor', COALESCE(j.finished_at, j.updated_at), 1, j.repo, j.pr_url, NULL
FROM jobs j WHERE j.resume_attempts >= 2 AND j.status IN ('completed', 'pr_merged')
UNION ALL
SELECT 'followup_merged', m.at, 1, m.label, m.pr_url, NULL FROM merged m WHERE m.pipeline_template = 'pr-followup'
UNION ALL
SELECT 'iron_will', f.at, 1, f.label, f.pr_url, NULL
FROM (SELECT DISTINCT ON (issue_id) * FROM merged WHERE issue_id IS NOT NULL ORDER BY issue_id, at) f
WHERE (SELECT count(*) FROM jobs e WHERE e.issue_id = f.issue_id AND e.created_at <= f.created_at) >= 3
-- oddities
UNION ALL
SELECT 'weekend_merge', m.at, 1, m.label, m.pr_url, NULL FROM merged m
WHERE extract(isodow FROM m.at AT TIME ZONE 'UTC') IN (6, 7)
UNION ALL
SELECT 'midnight_merge', m.at, 1, m.label, m.pr_url, NULL FROM merged m
WHERE extract(hour FROM m.at AT TIME ZONE 'UTC') < 4
UNION ALL
SELECT 'friday_night', m.at, 1, m.label, m.pr_url, NULL FROM merged m
WHERE extract(isodow FROM m.at AT TIME ZONE 'UTC') = 5 AND extract(hour FROM m.at AT TIME ZONE 'UTC') >= 20
UNION ALL
SELECT 'holiday_merge', m.at, 1, m.label, m.pr_url, NULL FROM merged m
WHERE to_char(m.at AT TIME ZONE 'UTC', 'MM-DD') IN ('12-25', '10-31', '01-01')
UNION ALL
SELECT 'long_grind', m.at, 1, m.label, m.pr_url, NULL FROM merged m
WHERE m.finished_at - m.started_at > INTERVAL '2 hours'
UNION ALL
SELECT 'pet_drop', m.at, 1, m.label, m.pr_url, NULL FROM merged m
WHERE m.repo IS NOT NULL AND NOT EXISTS (SELECT 1 FROM jobs e WHERE e.repo = m.repo AND e.created_at < m.created_at)
UNION ALL
SELECT 'ninety_nine', COALESCE(c.merged_at, c.verified_at), 1, c.title, c.tracker_url, NULL FROM healing_cases c
WHERE c.occurrence_count >= 99 AND COALESCE(c.merged_at, c.verified_at) IS NOT NULL
ORDER BY at
"""


@dataclass(frozen=True)
class JobRow:
    """One job, for quests and repo diaries (spec 2026-10-24)."""

    repo: str
    issue_id: str | None
    template: str | None
    status: str
    created_at: datetime
    #: When it finished, else when it last changed.
    at: datetime
    #: A completed context audit's report, for its outdated claims; None for every other job.
    report: str | None = None


@dataclass(frozen=True)
class RunRow:
    """One agent run that ended without an error, with its job's repo and issue."""

    agent_type: str
    repo: str
    issue_id: str | None
    at: datetime


@dataclass(frozen=True)
class Journal:
    jobs: list[JobRow]
    runs: list[RunRow]


_JOBS_SQL = """
SELECT j.repo, j.issue_id, j.pipeline_template AS template, j.status, j.created_at,
       COALESCE(j.delivery_state_at, j.finished_at, j.updated_at) AS at,
       CASE WHEN j.pipeline_template = 'context-audit' AND j.status = 'completed' THEN j.result_summary END AS report
FROM jobs j
WHERE j.repo IS NOT NULL
ORDER BY at
"""

_RUNS_SQL = """
SELECT t.agent_type, j.repo, j.issue_id, t.created_at AS at
FROM traces t JOIN jobs j ON j.job_id = t.job_id
WHERE t.error_code IS NULL AND t.result = 'success' AND t.agent_type IS NOT NULL AND j.repo IS NOT NULL
ORDER BY t.created_at
"""
