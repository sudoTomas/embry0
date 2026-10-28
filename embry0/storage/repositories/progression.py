"""The rows progression reads (spec 2026-10-24): the events that earn experience, and the facts achievements count.

Read-only, from tables other features already write: nothing here stores
experience, so the same rows always read as the same levels. An event row
is ``(source, agent_type, at, report, tools_called, skill_actions)``:
``source`` names the event kind the progression service prices,
``agent_type`` the character that earned it, ``at`` when it happened,
``report`` the job's report where the price depends on it (a context
audit's outdated claims), and for a run the tool calls it made, by name
and by action key.

The facts query is built from the active content pack (#176): one SQL
fragment per metric the pack counts, from :data:`FRAGMENTS`, with the
pack's parameters and timezone passed as bind values. No text of the pack
ever enters the SQL.
"""

from __future__ import annotations

import string
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from embry0.storage.database import DatabasePool

# The character a job's code is credited to: the Slopmaster for a slop cleanup
# (spec 2026-10-25, slop dungeon), the developer otherwise.
_CODER = "CASE WHEN {job}.pipeline_template = 'slop-cleanup' THEN 'slopmaster' ELSE 'developer' END"

_EVENTS_SQL = f"""
-- an agent run that ended without an error, with the tool calls it made
SELECT 'run' AS source, t.agent_type, t.created_at AS at, NULL::text AS report, t.tools_called, t.skill_actions
FROM traces t
WHERE t.error_code IS NULL AND t.result = 'success' AND t.agent_type IS NOT NULL
UNION ALL
-- the developer (the Slopmaster, for a slop cleanup): a pull request a job opened was merged
SELECT 'merged', {_CODER.format(job="j")}, COALESCE(j.delivery_state_at, j.finished_at, j.updated_at), NULL, NULL, NULL
FROM jobs j WHERE j.status = 'pr_merged'
UNION ALL
-- the developer (or the Slopmaster): a job that opened a pull request
SELECT 'opened', {_CODER.format(job="j")}, COALESCE(j.finished_at, j.updated_at), NULL, NULL, NULL
FROM jobs j WHERE j.pr_url IS NOT NULL
UNION ALL
-- the Slopmaster: a merged slop cleanup, with the lines it added and removed as its report
SELECT 'cleaned', 'slopmaster', COALESCE(j.delivery_state_at, j.finished_at, j.updated_at),
       (j.work_nature -> 'cleanup')::text, NULL, NULL
FROM jobs j WHERE j.status = 'pr_merged' AND j.pipeline_template = 'slop-cleanup'
UNION ALL
-- the developer (or the Slopmaster): a merge on an issue whose earlier job had failed
SELECT 'comeback', {_CODER.format(job="m")}, COALESCE(m.delivery_state_at, m.finished_at, m.updated_at), NULL, NULL, NULL
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
        rows: list[Any] = await self._db.fetch(_EVENTS_SQL)
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

    async def facts(self, queries: Iterable[FactFragment], timezone: str) -> list[Fact]:
        """Every step of every metric ``queries`` asks for, with its evidence, oldest first.

        Each fact's ``metric`` is its query's key, so two achievements that
        count one metric with different parameters count apart.
        """
        queries = list(queries)
        if not queries:
            return []
        sql, args = facts_sql(queries, timezone)
        rows: list[Any] = await self._db.fetch(sql, *args)
        return [
            Fact(queries[r["fragment"]].key, r["at"], float(r["value"]), r["label"], r["url"], r["report"])
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


class FactFragment(Protocol):
    """One fragment the facts query runs: the key its rows are filed under, the fragment, its parameters."""

    @property
    def key(self) -> str: ...

    @property
    def fragment(self) -> str: ...

    @property
    def params(self) -> Mapping[str, Any]: ...


# Every metric is an outcome (spec 2026-10-25, the achievement diary). A merged
# pull request is one the forge confirmed merged, or whose job says so.
_FACTS_CTES = """
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
"""

#: One fragment per metric (``progression_vocabulary.METRICS[...].fragment``): a SELECT of
#: ``(at, value, label, url, report)``. ``{name}`` is a parameter of the metric and ``{tz}``
#: the pack's timezone; :func:`facts_sql` binds each as a value.
FRAGMENTS: dict[str, str] = {
    # shipping
    "merged": "SELECT m.at, 1, m.label, m.pr_url, NULL FROM merged m",
    "first_try": """
        SELECT m.at, 1, m.label, m.pr_url, NULL FROM merged m
        WHERE NOT EXISTS (
            SELECT 1 FROM jobs e
            WHERE e.job_id <> m.job_id AND e.created_at < m.created_at
              AND ((m.issue_id IS NOT NULL AND e.issue_id = m.issue_id) OR (m.pr_url IS NOT NULL AND e.pr_url = m.pr_url))
        )""",
    "clean_kill": """
        SELECT m.at, 1, m.label, m.pr_url, NULL FROM merged m
        WHERE NOT EXISTS (
            SELECT 1 FROM traces t WHERE t.job_id = m.job_id AND (t.result <> 'success' OR t.error_code IS NOT NULL)
        )""",
    "merged_under_cost": """
        SELECT m.at, 1, m.label, m.pr_url, NULL FROM merged m
        WHERE m.total_cost_usd > 0 AND m.total_cost_usd < {max_usd}::float8""",
    "merged_within": """
        SELECT m.at, 1, m.label, m.pr_url, NULL FROM merged m
        WHERE m.at - m.created_at < make_interval(secs => {hours}::float8 * 3600)""",
    "repo_hopper": """
        SELECT f.at, 1, f.repo, f.pr_url, NULL
        FROM (SELECT DISTINCT ON (repo) repo, at, pr_url FROM merged WHERE repo IS NOT NULL ORDER BY repo, at) f""",
    "area_merged": "SELECT m.at, 1, m.label || ' · ' || m.area, m.pr_url, NULL FROM merged m WHERE m.area IS NOT NULL",
    "merges_in_a_day": """
        SELECT max(m.at), 1, to_char(max(m.at) AT TIME ZONE {tz}, 'YYYY-MM-DD'), NULL, NULL
        FROM merged m GROUP BY date_trunc('day', m.at AT TIME ZONE {tz}) HAVING count(*) >= {count}::int""",
    "merged_reviewed_and_tested": """
        SELECT m.at, 1, m.label, m.pr_url, NULL FROM merged m
        WHERE EXISTS (SELECT 1 FROM traces t WHERE t.job_id = m.job_id
                      AND t.agent_type = ANY({review_agents}::text[]) AND t.result = 'success')
          AND EXISTS (SELECT 1 FROM traces t WHERE t.job_id = m.job_id
                      AND t.agent_type = ANY({qa_agents}::text[]) AND t.result = 'success')""",
    # hunting
    "healed": """
        SELECT h.healed_at, 1, h.title, h.tracker_url, NULL FROM healed h
        WHERE ({severity}::text IS NULL OR h.severity = {severity}::text)
          AND ({min_seen}::int IS NULL OR h.occurrence_count >= {min_seen}::int)""",
    "never_respawned": """
        SELECT h.healed_at, 1, h.title, h.tracker_url, NULL FROM healed h
        WHERE NOT EXISTS (
            SELECT 1 FROM healing_case_events e
            WHERE e.case_id = h.id AND e.event = 'recurrence' AND e.occurred_at > h.healed_at
        )""",
    "out_of_reach": """
        SELECT c.merged_at, 1, c.title, c.tracker_url, NULL FROM healing_cases c
        WHERE c.origin = 'watcher' AND c.merged_at IS NOT NULL AND COALESCE(c.resolution_path, 'embry0') = 'embry0'""",
    "cases_accepted": """
        SELECT c.accepted_at, 1, c.title, c.tracker_url, NULL FROM healing_cases c
        WHERE c.origin = 'watcher' AND c.accepted_at IS NOT NULL""",
    "deployed_fix": """
        SELECT c.deployed_at, 1, c.title, c.tracker_url, NULL FROM healing_cases c WHERE c.deployed_at IS NOT NULL""",
    "fixed_recurring": """
        SELECT COALESCE(c.merged_at, c.verified_at), 1, c.title, c.tracker_url, NULL FROM healing_cases c
        WHERE c.occurrence_count >= {min_seen}::int AND COALESCE(c.merged_at, c.verified_at) IS NOT NULL""",
    # questing
    "issue_done": """
        SELECT f.at, 1, COALESCE(i.title, f.label), f.pr_url, NULL
        FROM (SELECT DISTINCT ON (issue_id) * FROM merged WHERE issue_id IS NOT NULL ORDER BY issue_id, at) f
        LEFT JOIN issues i ON i.id = f.issue_id""",
    "side_quest": """
        SELECT f.at, 1, COALESCE(i.title, f.label), f.pr_url, NULL
        FROM (SELECT DISTINCT ON (issue_id) * FROM merged WHERE issue_id IS NOT NULL ORDER BY issue_id, at) f
        JOIN issues i ON i.id = f.issue_id WHERE i.parent_issue_id IS NOT NULL""",
    "owner_answer": "SELECT d.created_at, 1, d.repo || ' · ' || d.decision, NULL, NULL FROM proposal_decisions d",
    "proposal_accepted": """
        SELECT d.created_at, 1, d.repo, NULL, NULL FROM proposal_decisions d WHERE d.decision = 'accept'""",
    "proposal_merged": """
        SELECT m.at, 1, m.label, m.pr_url, NULL
        FROM proposal_decisions d JOIN merged m ON m.job_id = d.job_id WHERE d.decision = 'accept'""",
    "question_answered": """
        SELECT q.answered_at, 1, left(q.question, 120), NULL, NULL
        FROM issue_inputs q WHERE q.answered_at IS NOT NULL AND q.answer IS NOT NULL""",
    # lore: a completed context audit, with its report; the service reads its claims
    "audit": """
        SELECT COALESCE(j.finished_at, j.updated_at), 1, j.repo, NULL, j.result_summary
        FROM jobs j WHERE j.pipeline_template = 'context-audit' AND j.status = 'completed'""",
    "audit_merged": "SELECT m.at, 1, m.label, m.pr_url, NULL FROM merged m WHERE m.pipeline_template = 'context-audit'",
    "areas_mapped": """
        SELECT f.at, 1, f.repo, f.pr_url, NULL
        FROM (SELECT DISTINCT ON (repo) repo, at, pr_url FROM merged WHERE pipeline_template = 'map-areas' ORDER BY repo, at) f""",
    "design_score": """
        SELECT s.last_audit_at, 1, s.repo || ' · ' || s.kind || ' ' || s.score, NULL, NULL
        FROM repo_standard_state s WHERE s.score >= {min_score}::int AND s.last_audit_at IS NOT NULL""",
    # guarding
    "reviews": """
        SELECT t.created_at, 1, COALESCE(j.repo, t.agent_type), j.pr_url, NULL
        FROM traces t LEFT JOIN jobs j ON j.job_id = t.job_id
        WHERE t.agent_type = ANY({agents}::text[]) AND t.result = 'success' AND t.error_code IS NULL""",
    "review_posted": """
        SELECT r.posted_at, 1, r.repo || ' #' || r.number, NULL, NULL FROM pr_reviews r WHERE r.posted_at IS NOT NULL""",
    "qa_passed": """
        SELECT COALESCE(j.finished_at, j.updated_at), 1, j.repo, NULL, NULL
        FROM jobs j WHERE j.pipeline_template = 'qa' AND j.status = 'completed'""",
    "gate_blocked": """
        SELECT COALESCE(j.finished_at, j.updated_at), 1, j.repo, j.pr_url, NULL
        FROM jobs j WHERE j.error_code = 'ERR_VERIFY_NEW_TEST_FAILURES'""",
    "setup_applied": """
        SELECT COALESCE(p.decided_at, p.updated_at), 1, p.repo, NULL, NULL
        FROM repo_setup_proposals p WHERE p.status = 'applied'""",
    # grit
    "comeback": """
        SELECT m.at, 1, m.label, m.pr_url, NULL FROM merged m
        WHERE m.issue_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM jobs f WHERE f.issue_id = m.issue_id AND f.status = 'failed' AND f.created_at < m.created_at
        )""",
    "resumed": """
        SELECT COALESCE(j.finished_at, j.updated_at), 1, j.repo, j.pr_url, NULL
        FROM jobs j WHERE j.resume_attempts >= {min_resumes}::int AND j.status IN ('completed', 'pr_merged')""",
    "followup_merged": "SELECT m.at, 1, m.label, m.pr_url, NULL FROM merged m WHERE m.pipeline_template = 'pr-followup'",
    "merged_on_attempt": """
        SELECT f.at, 1, f.label, f.pr_url, NULL
        FROM (SELECT DISTINCT ON (issue_id) * FROM merged WHERE issue_id IS NOT NULL ORDER BY issue_id, at) f
        WHERE (SELECT count(*) FROM jobs e WHERE e.issue_id = f.issue_id AND e.created_at <= f.created_at)
              >= {min_attempts}::int""",
    # oddities, on the pack's calendar
    "merged_at_local_time": """
        SELECT m.at, 1, m.label, m.pr_url, NULL FROM merged m
        WHERE extract(isodow FROM m.at AT TIME ZONE {tz})::int = ANY({weekdays}::int[])
          AND extract(hour FROM m.at AT TIME ZONE {tz})::int >= {from_hour}::int
          AND extract(hour FROM m.at AT TIME ZONE {tz})::int < {to_hour}::int""",
    "merged_on_dates": """
        SELECT m.at, 1, m.label, m.pr_url, NULL FROM merged m
        WHERE to_char(m.at AT TIME ZONE {tz}, 'MM-DD') = ANY({dates}::text[])""",
    "merged_running_over": """
        SELECT m.at, 1, m.label, m.pr_url, NULL FROM merged m
        WHERE m.finished_at - m.started_at > make_interval(secs => {hours}::float8 * 3600)""",
    "lucky_find": """
        SELECT m.at, 1, m.label, m.pr_url, NULL FROM merged m
        WHERE m.repo IS NOT NULL AND NOT EXISTS (SELECT 1 FROM jobs e WHERE e.repo = m.repo AND e.created_at < m.created_at)""",
}


def facts_sql(queries: Iterable[FactFragment], timezone: str) -> tuple[str, list[Any]]:
    """The facts query for ``queries``, and its bind values: the timezone and every parameter, never inlined.

    Each fragment's rows carry its position in ``queries`` as ``fragment``,
    a number the code generates, so the caller files them under the
    query's key.
    """
    args: list[Any] = []
    parts: list[str] = []
    for index, query in enumerate(queries):
        template = FRAGMENTS[query.fragment]
        names = {field for _, field, _, _ in string.Formatter().parse(template) if field}
        slots: dict[str, str] = {}
        for name in sorted(names):
            if name == "tz":
                args.append(timezone)
                slots[name] = f"${len(args)}::text"
            else:
                value = query.params.get(name)
                args.append(list(value) if isinstance(value, (list, tuple)) else value)
                slots[name] = f"${len(args)}"
        body = template.format(**slots)
        parts.append(
            f"SELECT {index}::int AS fragment, q.at::timestamptz AS at, q.value::float8 AS value, "
            f"q.label::text AS label, q.url::text AS url, q.report::text AS report "
            f"FROM ({body}) AS q(at, value, label, url, report)"
        )
    return _FACTS_CTES + "\nUNION ALL\n".join(parts) + "\nORDER BY at", args


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
