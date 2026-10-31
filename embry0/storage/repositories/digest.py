"""What embry0 did in a day, and the once-per-day claim on sending it.

The operator asked for one daily summary in place of a notification per watcher
analysis job, which arrived hourly as raw JSON. Every number here comes from
tables that already record the facts — jobs, background_spend, watcher_runs,
healing_cases, issue_inputs — so the digest is a read, never a second source of
truth.

The watcher's own analysis jobs are counted separately from real work. They are
~24 a day and would otherwise bury the two or three jobs a human dispatched.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from embry0.storage.database import DatabasePool

#: How many individual jobs the digest names. The counts are always complete;
#: this only bounds the list a human reads.
MAX_LISTED_JOBS = 8

#: What the background features spent outside any job in [$1, $2), by feature:
#: the proposal and job briefs and the Slop-O-Meter's judge, which record each
#: call in ``background_spend`` (migration 135) rather than on a job. The
#: window is a range of ``idx_background_spend_at``, so the read stays one
#: day's rows however long the history grows.
_BACKGROUND_SPEND_SQL = """
SELECT feature, COALESCE(sum(cost_usd), 0) AS cost
  FROM background_spend
 WHERE at >= $1 AND at < $2
 GROUP BY feature
 ORDER BY feature
"""

# Listed jobs are every finished job but cancelled ones. The status vocabulary
# is wider than completed/failed: a job whose PR merged is `pr_merged`, one
# whose PR was closed is `pr_closed`, and there is `partial` — a first draft
# listed only completed/failed and would have left out the day's merged PR.


class DigestRepository:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def claim_day(self, day: date) -> bool:
        """True for exactly one caller per UTC day."""
        row = await self._db.fetchrow(
            "INSERT INTO digest_deliveries (day) VALUES ($1) ON CONFLICT DO NOTHING RETURNING day",
            day,
        )
        return row is not None

    async def mark_sent(self, day: date, payload: dict[str, Any]) -> None:
        await self._db.execute(
            "UPDATE digest_deliveries SET sent_at = NOW(), payload = $2::jsonb WHERE day = $1",
            day,
            # A plain-JSON copy (dates become strings); the pool's JSONB codec
            # encodes it once. json.dumps here stored a JSON *string* (#382).
            json.loads(json.dumps(payload, default=str)),
        )

    async def release_day(self, day: date) -> None:
        """Give the day back when sending failed, so the next pass retries it."""
        await self._db.execute("DELETE FROM digest_deliveries WHERE day = $1 AND sent_at IS NULL", day)

    async def collect(self, *, since: datetime, until: datetime, watcher_actor: str) -> dict[str, Any]:
        """Everything the digest reports for [since, until)."""
        # "Real" work excludes the watcher's own analysis jobs. Older rows
        # predate dispatched_by, so an issue-less, repo-less analysis job
        # counts as the watcher's too.
        #
        # COALESCE is load bearing: most rows have dispatched_by NULL, and
        # `NULL = $3` is NULL, so without it `NOT (…)` is NULL too and every
        # such job silently drops out of BOTH halves — a test caught this
        # reporting zero jobs for a day with two.
        watcher_job = (
            "(COALESCE(dispatched_by, '') = $3"
            " OR (COALESCE(job_kind, '') = 'analysis' AND repo IS NULL AND issue_id IS NULL))"
        )
        jobs = await self._db.fetch(
            f"""
            SELECT status, count(*) AS n, COALESCE(sum(total_cost_usd), 0) AS cost
              FROM jobs
             WHERE finished_at >= $1 AND finished_at < $2 AND NOT {watcher_job}
             GROUP BY status
            """,
            since,
            until,
            watcher_actor,
        )
        listed = await self._db.fetch(
            f"""
            SELECT job_id, status, repo, task, pr_url, error_code, total_cost_usd
              FROM jobs
             WHERE finished_at >= $1 AND finished_at < $2 AND NOT {watcher_job}
               AND status NOT IN ('cancelled')
             ORDER BY finished_at DESC
             LIMIT {MAX_LISTED_JOBS}
            """,
            since,
            until,
            watcher_actor,
        )
        watcher_spend = await self._db.fetchval(
            f"""
            SELECT COALESCE(sum(total_cost_usd), 0) FROM jobs
             WHERE finished_at >= $1 AND finished_at < $2 AND {watcher_job}
            """,
            since,
            until,
            watcher_actor,
        )
        background = await self._db.fetch(_BACKGROUND_SPEND_SQL, since, until)
        ticks = await self._db.fetch(
            "SELECT action, count(*) AS n FROM watcher_runs WHERE created_at >= $1 AND created_at < $2 GROUP BY action",
            since,
            until,
        )
        opened = await self._db.fetch(
            "SELECT origin, count(*) AS n FROM healing_cases WHERE detected_at >= $1 AND detected_at < $2 GROUP BY origin",
            since,
            until,
        )
        closed = await self._db.fetch(
            """
            SELECT COALESCE(resolution_path, 'unknown') AS path, count(*) AS n FROM healing_cases
             WHERE closed_at >= $1 AND closed_at < $2 GROUP BY 1
            """,
            since,
            until,
        )
        verdicts = await self._db.fetch(
            """
            SELECT healed_verdict AS verdict, count(*) AS n FROM healing_cases
             WHERE verdict_checked_at >= $1 AND verdict_checked_at < $2 GROUP BY 1
            """,
            since,
            until,
        )
        open_cases = await self._db.fetch(
            "SELECT stage, count(*) AS n FROM healing_cases WHERE stage <> 'closed' GROUP BY stage"
        )
        pending_questions = await self._db.fetchval(
            "SELECT count(*) FROM issue_inputs WHERE status = 'pending' AND importance = 'blocking'"
        )

        return {
            "since": since.isoformat(),
            "until": until.isoformat(),
            "jobs": {r["status"]: int(r["n"]) for r in jobs},
            "job_spend_usd": round(sum(float(r["cost"]) for r in jobs), 2),
            "watcher_spend_usd": round(float(watcher_spend or 0), 2),
            "background_spend_usd": round(sum(float(r["cost"]) for r in background), 2),
            "background_spend_by_feature": {r["feature"]: round(float(r["cost"]), 2) for r in background},
            "jobs_listed": [
                {
                    "job_id": r["job_id"],
                    "status": r["status"],
                    "repo": r["repo"],
                    "title": _first_line(r["task"]),
                    "pr_url": r["pr_url"],
                    "error_code": r["error_code"],
                    "cost_usd": round(float(r["total_cost_usd"] or 0), 2),
                }
                for r in listed
            ],
            "watcher_ticks": {r["action"]: int(r["n"]) for r in ticks},
            "cases_opened": {r["origin"]: int(r["n"]) for r in opened},
            "cases_closed": {r["path"]: int(r["n"]) for r in closed},
            "verdicts": {r["verdict"]: int(r["n"]) for r in verdicts},
            "cases_open_by_stage": {r["stage"]: int(r["n"]) for r in open_cases},
            "pending_questions": int(pending_questions or 0),
        }


def _first_line(task: Any, limit: int = 120) -> str:
    text = str(task or "").strip().splitlines()
    line = text[0].strip() if text else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"
