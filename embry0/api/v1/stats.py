"""Stats API — aggregated metrics compatible with frontend dashboard."""

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request

from embry0.api.deps import get_db
from embry0.cache.prebaked_staleness import summarize_prebuilt_images
from embry0.orchestration import dispatch_actors
from embry0.safety.error_codes import failure_categories
from embry0.storage.database import DatabasePool

router = APIRouter()
logger = structlog.get_logger(__name__)


async def _qa_prebuilt_images(request: Request) -> dict[str, object] | None:
    """#334: which repos' pre-built QA images sit on a rebuilt base. None when
    the stack has no docker handle (tests, degraded start) or the summary
    fails — an optional field must not take /stats down."""
    state = request.app.state
    try:
        return await summarize_prebuilt_images(
            getattr(state, "qa_image_tags_repo", None),
            getattr(state, "qa_base_digest_resolver", None),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("stats_qa_prebuilt_images_failed", error=str(exc)[:200])
        return None


def _quarantine_active(row: dict[str, object]) -> bool:
    """A past ``quarantined_until`` is usable again and must not count."""
    if row.get("status") != "quarantined":
        return False
    until = row.get("quarantined_until")
    if until is None:
        return True
    if isinstance(until, datetime):
        if until.tzinfo is None:
            until = until.replace(tzinfo=UTC)
        return until > datetime.now(UTC)
    return True


async def _credentials_summary(request: Request) -> dict[str, object] | None:
    """Spec 2026-09-29 §3.8: registry size, quarantined/disabled counts and which
    env keys the fallback is still serving. None when the registry is not wired
    or the read fails — an optional field must not take /stats down."""
    state = request.app.state
    repo = getattr(state, "credentials_repo", None)
    if repo is None:
        return None
    try:
        rows = await repo.list_all()
        resolver = getattr(state, "credential_resolver", None)
        return {
            "count": len(rows),
            "quarantined": sum(1 for r in rows if _quarantine_active(r)),
            "disabled": sum(1 for r in rows if r.get("status") == "disabled"),
            "env_fallback_keys": list(resolver.env_fallback_keys()) if resolver is not None else [],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("stats_credentials_failed", error=str(exc)[:200])
        return None


#: The service actors whose jobs are a background feature's (#164): their spend is that feature's.
_BACKGROUND_JOB_ACTORS: dict[str, str] = {
    dispatch_actors.WATCHER: "watcher",
    dispatch_actors.CONTEXT_AUDIT: "context_audit",
    dispatch_actors.SCOUT: "scout",
}

# The jobs the background features dispatched this month, read as a range of
# idx_jobs_dispatched_by_created_at (dispatched_by, created_at): a job counts
# in the month, and the day, it was created in.
_BACKGROUND_JOBS_SQL = """
SELECT
    dispatched_by,
    COUNT(*) AS jobs,
    COALESCE(SUM(total_cost_usd) FILTER (WHERE created_at >= date_trunc('day', NOW())), 0) AS daily_usd,
    COALESCE(SUM(total_cost_usd), 0) AS monthly_usd
FROM jobs
WHERE dispatched_by = ANY($1::text[]) AND created_at >= date_trunc('month', NOW())
GROUP BY dispatched_by
"""


# Every background call's spend since the table began, for the lifetime total: a
# scalar subquery of the stats query, so it costs no round-trip of its own, and
# an index-only scan of idx_background_spend_at, which INCLUDEs cost_usd. The
# table gets a row per brief or judgment, a few a day.
_BACKGROUND_TOTAL_SQL = "(SELECT COALESCE(SUM(b.cost_usd), 0) FROM background_spend b)"


async def _background_spend(db: DatabasePool) -> dict[str, Any]:
    """What each background feature spent today and this month (#164), the windows the budget caps count.

    ``daily_usd`` and ``monthly_usd`` are the calls the briefs and the
    Slop-O-Meter's judge made outside any job (``background_spend``), which
    the day's and the month's totals add to what jobs spent. ``features``
    lists every feature's spend: those calls, and the jobs the watcher, the
    context audit and the scout dispatched, which the totals already count
    as jobs (``source`` says which). Both reads are bounded by the month, so
    they stay a range of an index however long the history grows. The
    lifetime total is read with the stats query (:data:`_BACKGROUND_TOTAL_SQL`).
    """
    from embry0.storage.repositories.background_spend import BackgroundSpendRepository

    calls = await BackgroundSpendRepository(db).this_month()
    job_rows = await db.fetch(_BACKGROUND_JOBS_SQL, list(_BACKGROUND_JOB_ACTORS))
    features = [{**c, "source": "calls"} for c in calls]
    features += [
        {
            "feature": _BACKGROUND_JOB_ACTORS[str(r["dispatched_by"])],
            "source": "jobs",
            "jobs": int(r["jobs"]),
            "daily_usd": float(r["daily_usd"]),
            "monthly_usd": float(r["monthly_usd"]),
        }
        for r in job_rows
    ]
    return {
        "daily_usd": sum(c["daily_usd"] for c in calls),
        "monthly_usd": sum(c["monthly_usd"] for c in calls),
        "features": sorted(features, key=lambda f: (-f["monthly_usd"], f["feature"], f["source"])),
    }


@router.get("/stats")
async def get_stats(request: Request, db: DatabasePool = Depends(get_db)) -> dict[str, object]:
    # Aggregate job counts + lifetime cost + live state
    row = await db.fetchrow(
        """
        SELECT
            COUNT(*) AS total_jobs,
            COUNT(*) FILTER (WHERE status = 'completed') AS completed,
            COUNT(*) FILTER (WHERE status = 'failed') AS failed,
            COUNT(*) FILTER (WHERE status = 'partial') AS partial,
            COUNT(*) FILTER (WHERE status = 'running') AS running,
            COUNT(*) FILTER (WHERE status IN ('pending', 'queued')) AS queued,
            COUNT(*) FILTER (WHERE status = 'awaiting_input') AS awaiting_input,
            COUNT(*) FILTER (WHERE status = 'paused') AS paused,
            COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled,
            COUNT(*) FILTER (WHERE status = 'skipped') AS skipped,
            COUNT(*) FILTER (WHERE status IN ('pr_merged', 'pr_closed')) AS completed_with_pr_outcome,
            COUNT(*) FILTER (WHERE delivery_state = 'pr_open') AS delivery_pr_open,
            COUNT(*) FILTER (WHERE delivery_state = 'merged') AS delivery_merged,
            COUNT(*) FILTER (WHERE delivery_state = 'closed') AS delivery_closed,
            -- Jobs and pull requests are not the same population: several
            -- jobs can share one PR (a retry, a follow-up dispatched onto the
            -- branch), so counting rows over-reports PRs. Anything phrased as
            -- "PRs" counts DISTINCT urls.
            COUNT(DISTINCT pr_url) FILTER (WHERE delivery_state = 'merged') AS prs_merged,
            COUNT(DISTINCT pr_url) FILTER (WHERE delivery_state = 'closed') AS prs_closed,
            COUNT(DISTINCT pr_url) FILTER (WHERE delivery_state = 'pr_open') AS prs_open,
            COUNT(*) FILTER (
                WHERE delivery_state = 'merged' AND status NOT IN ('completed', 'pr_merged', 'pr_closed')
            ) AS delivered_despite_status,
            COUNT(*) FILTER (
                WHERE status IN ('completed', 'pr_merged', 'pr_closed') OR delivery_state = 'merged'
            ) AS succeeded_or_delivered,
            COALESCE(SUM(total_cost_usd), 0) AS total_cost,
            COALESCE(SUM(total_cost_usd) FILTER (
                WHERE started_at >= date_trunc('day', NOW())
            ), 0) AS daily_cost,
            COALESCE(SUM(total_cost_usd) FILTER (
                WHERE started_at >= date_trunc('month', NOW())
            ), 0) AS monthly_cost,
            -- #164: what background calls spent outside any job, ever.
            """
        + _BACKGROUND_TOTAL_SQL
        + """ AS background_total
        FROM jobs
        """
    )
    total_jobs = row["total_jobs"] if row else 0
    completed = row["completed"] if row else 0
    failed = row["failed"] if row else 0
    partial = row["partial"] if row else 0
    running = row["running"] if row else 0
    queued = row["queued"] if row else 0
    awaiting_input = row["awaiting_input"] if row else 0
    paused = row["paused"] if row else 0
    background = await _background_spend(db)
    # #164: what background features spent outside any job counts in every
    # spend reported: the lifetime total, and the day's and the month's, as
    # the budget caps count them.
    background["total_usd"] = float((row.get("background_total") if row else 0.0) or 0.0)
    total_cost = float(row["total_cost"] if row else 0.0) + background["total_usd"]
    daily_cost = float(row["daily_cost"] if row else 0.0) + background["daily_usd"]
    monthly_cost = float(row["monthly_cost"] if row else 0.0) + background["monthly_usd"]
    # #280: two outcomes, reported apart. How the JOB ended — `pr_merged` and
    # `pr_closed` are completed jobs whose PR was later merged / closed, so
    # they count as the job succeeding (the old `completed / total` dropped a
    # job out of the numerator the moment its PR merged). And what became of
    # the PULL REQUEST, whatever the job's status. `success_rate` counts a job
    # as a success when it completed OR its PR merged: a job that failed a
    # late stage after opening a PR that then merged delivered its work.
    stats = dict(row) if row else {}
    job_succeeded = completed + int(stats.get("completed_with_pr_outcome", 0))
    delivery_merged = int(stats.get("delivery_merged", 0))
    delivery_closed = int(stats.get("delivery_closed", 0))
    delivery_pr_open = int(stats.get("delivery_pr_open", 0))
    with_pr = delivery_merged + delivery_closed + delivery_pr_open
    # #474: a job skipped because someone else's open PR already covered its
    # ticket did no work — neither a success nor a failure — so the rates are
    # over the jobs that did.
    attempted = total_jobs - int(stats.get("skipped", 0))
    job_success_rate = job_succeeded / attempted if attempted > 0 else 0.0
    success_rate = int(stats.get("succeeded_or_delivered", 0)) / attempted if attempted > 0 else 0.0
    prs_merged = int(stats.get("prs_merged", 0))
    prs_closed = int(stats.get("prs_closed", 0))
    settled_prs = prs_merged + prs_closed
    delivery = {
        # Jobs, by what became of the PR they produced.
        "none": total_jobs - with_pr,
        "pr_open": delivery_pr_open,
        "merged": delivery_merged,
        "closed": delivery_closed,
        # Jobs whose PR merged although their own status says anything but
        # success. Note this counts embry0's work that a PERSON finished: on
        # the 2026-09-20 rows, 5 of the merged PRs matched to a failed job
        # were opened by a human from that job's branch. Delivered, yes;
        # autonomous throughput, no.
        "merged_despite_job_status": int(stats.get("delivered_despite_status", 0)),
        # Distinct pull requests, which is a different population from jobs:
        # a retry and a follow-up share one PR.
        "prs_merged": prs_merged,
        "prs_closed": prs_closed,
        "prs_open": int(stats.get("prs_open", 0)),
        # Of the PRs that reached an outcome, the share that merged.
        "merge_rate": prs_merged / settled_prs if settled_prs > 0 else 0.0,
    }
    total_issues = await db.fetchval("SELECT COUNT(*) FROM issues") or 0

    # #281: failed jobs grouped by failure category. The code → category map
    # lives next to the codes; NULL / unrecognised codes land in "unknown".
    failure_rows = await db.fetch(
        "SELECT error_code, COUNT(*) AS n FROM jobs WHERE status = 'failed' GROUP BY error_code"
    )
    failures_by_category = failure_categories((r["error_code"], r["n"]) for r in failure_rows)

    # Recent issues (10 most recent by latest job start, falling back to created_at).
    # Each row names its issue (title, tracker key) and keeps the issue's own
    # status apart from its latest job's: `status` blends the two and
    # `issue_number` is 0 for every non-GitHub tracker, so both stay only for
    # older clients.
    recent_rows = await db.fetch(
        """
        SELECT
            i.id          AS trace_id,
            COALESCE(CASE WHEN i.tracker = 'github' THEN i.tracker_key::integer END, 0) AS issue_number,
            i.repo        AS repo,
            COALESCE(i.title, 'Untitled') AS title,
            i.tracker     AS tracker,
            i.tracker_key AS tracker_key,
            i.status      AS issue_status,
            j.status      AS job_status,
            COALESCE(j.started_at, i.created_at) AS timestamp,
            COALESCE(j.status = 'completed', false) AS passed,
            COALESCE(j.total_cost_usd, 0.0) AS cost_usd,
            COALESCE(j.status, i.status) AS status
        FROM issues i
        LEFT JOIN LATERAL (
            SELECT started_at, status, total_cost_usd
            FROM jobs
            WHERE jobs.issue_id = i.id
            ORDER BY started_at DESC NULLS LAST
            LIMIT 1
        ) j ON true
        ORDER BY COALESCE(j.started_at, i.created_at) DESC
        LIMIT 10
        """
    )
    recent_issues = [
        {
            "trace_id": r["trace_id"],
            "issue_number": r["issue_number"],
            "repo": r["repo"],
            "title": r["title"],
            "tracker": r["tracker"],
            "tracker_key": r["tracker_key"],
            "issue_status": r["issue_status"],
            "job_status": r["job_status"],
            "timestamp": r["timestamp"].isoformat() if r["timestamp"] else None,
            "passed": r["passed"],
            "cost_usd": float(r["cost_usd"]),
            "status": r["status"],
        }
        for r in recent_rows
    ]

    # Top 5 most expensive issues (sum of all jobs per issue)
    expensive_rows = await db.fetch(
        """
        SELECT
            i.id          AS trace_id,
            COALESCE(CASE WHEN i.tracker = 'github' THEN i.tracker_key::integer END, 0) AS issue_number,
            COALESCE(i.title, 'Untitled') AS title,
            i.repo        AS repo,
            COALESCE(SUM(j.total_cost_usd), 0.0) AS cost_usd,
            COUNT(j.job_id) AS job_count
        FROM issues i
        LEFT JOIN jobs j ON j.issue_id = i.id
        GROUP BY i.id, i.tracker, i.tracker_key, i.title, i.repo
        HAVING COALESCE(SUM(j.total_cost_usd), 0.0) > 0
        ORDER BY cost_usd DESC
        LIMIT 5
        """
    )
    top_expensive_issues = [
        {
            "trace_id": r["trace_id"],
            "issue_number": r["issue_number"],
            "title": r["title"],
            "repo": r["repo"],
            "cost_usd": float(r["cost_usd"]),
            "job_count": r["job_count"],
        }
        for r in expensive_rows
    ]

    # Cost breakdown per repo (top 8 by cost)
    repo_rows = await db.fetch(
        """
        SELECT
            repo,
            COALESCE(SUM(total_cost_usd), 0.0) AS cost_usd,
            COUNT(*) AS job_count
        FROM jobs
        WHERE repo IS NOT NULL
        GROUP BY repo
        HAVING COALESCE(SUM(total_cost_usd), 0.0) > 0
        ORDER BY cost_usd DESC
        LIMIT 8
        """
    )
    cost_by_repo = [
        {
            "repo": r["repo"],
            "cost_usd": float(r["cost_usd"]),
            "job_count": r["job_count"],
        }
        for r in repo_rows
    ]

    # EMB-35: token usage + cache-hit rate by agent phase, from traces.
    token_rows = await db.fetch(
        """
        SELECT
            agent_type,
            COUNT(*) AS runs,
            COALESCE(SUM(input_tokens), 0) AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
            COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens,
            COALESCE(SUM(cost_usd), 0.0) AS cost_usd
        FROM traces
        GROUP BY agent_type
        ORDER BY cost_usd DESC
        """
    )

    def _cache_hit_rate(cache_read: int, uncached_input: int) -> float:
        denom = cache_read + uncached_input
        return cache_read / denom if denom > 0 else 0.0

    tokens_by_agent = [
        {
            "agent_type": r["agent_type"],
            "runs": r["runs"],
            "input_tokens": int(r["input_tokens"]),
            "output_tokens": int(r["output_tokens"]),
            "cache_read_tokens": int(r["cache_read_tokens"]),
            "cache_creation_tokens": int(r["cache_creation_tokens"]),
            "cost_usd": float(r["cost_usd"]),
            "cache_hit_rate": _cache_hit_rate(int(r["cache_read_tokens"]), int(r["input_tokens"])),
        }
        for r in token_rows
    ]
    token_totals = {
        "input_tokens": sum(t["input_tokens"] for t in tokens_by_agent),
        "output_tokens": sum(t["output_tokens"] for t in tokens_by_agent),
        "cache_read_tokens": sum(t["cache_read_tokens"] for t in tokens_by_agent),
        "cache_creation_tokens": sum(t["cache_creation_tokens"] for t in tokens_by_agent),
    }
    token_totals["cache_hit_rate"] = _cache_hit_rate(token_totals["cache_read_tokens"], token_totals["input_tokens"])

    return {
        "total_issues": total_issues,
        "total_jobs": total_jobs,
        "completed": completed,
        "failed": failed,
        "partial": partial,
        "running": running,
        "queued": queued,
        "awaiting_input": awaiting_input,
        "paused": paused,
        "cancelled": int(stats.get("cancelled", 0)),
        "skipped": int(stats.get("skipped", 0)),
        "job_succeeded": job_succeeded,
        "success_rate": success_rate,
        "job_success_rate": job_success_rate,
        "delivery": delivery,
        "total_cost_usd": total_cost,
        "daily_cost_usd": daily_cost,
        "monthly_cost_usd": monthly_cost,
        "background_spend": background,
        "queue_depth": queued + running,
        "failure_categories": failures_by_category,
        "recent_issues": recent_issues,
        "top_expensive_issues": top_expensive_issues,
        "cost_by_repo": cost_by_repo,
        "tokens_by_agent": tokens_by_agent,
        "token_totals": token_totals,
        "qa_prebuilt_images": await _qa_prebuilt_images(request),
        "credentials": await _credentials_summary(request),
    }
