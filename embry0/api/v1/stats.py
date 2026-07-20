"""Stats API — aggregated metrics compatible with frontend dashboard."""

from fastapi import APIRouter, Depends

from embry0.api.deps import get_db
from embry0.storage.database import DatabasePool

router = APIRouter()


@router.get("/stats")
async def get_stats(db: DatabasePool = Depends(get_db)) -> dict[str, object]:
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
            COALESCE(SUM(total_cost_usd), 0) AS total_cost,
            COALESCE(SUM(total_cost_usd) FILTER (
                WHERE started_at >= date_trunc('day', NOW())
            ), 0) AS daily_cost,
            COALESCE(SUM(total_cost_usd) FILTER (
                WHERE started_at >= date_trunc('month', NOW())
            ), 0) AS monthly_cost
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
    total_cost = float(row["total_cost"] if row else 0.0)
    daily_cost = float(row["daily_cost"] if row else 0.0)
    monthly_cost = float(row["monthly_cost"] if row else 0.0)
    success_rate = completed / total_jobs if total_jobs > 0 else 0.0
    total_issues = await db.fetchval("SELECT COUNT(*) FROM issues") or 0

    # Recent issues (10 most recent by latest job start, falling back to created_at)
    recent_rows = await db.fetch(
        """
        SELECT
            i.id          AS trace_id,
            COALESCE(i.github_number, 0) AS issue_number,
            i.repo        AS repo,
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
            "tier": "standard",
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
            COALESCE(i.github_number, 0) AS issue_number,
            COALESCE(i.title, 'Untitled') AS title,
            i.repo        AS repo,
            COALESCE(SUM(j.total_cost_usd), 0.0) AS cost_usd,
            COUNT(j.job_id) AS job_count
        FROM issues i
        LEFT JOIN jobs j ON j.issue_id = i.id
        GROUP BY i.id, i.github_number, i.title, i.repo
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
        "success_rate": success_rate,
        "total_cost_usd": total_cost,
        "daily_cost_usd": daily_cost,
        "monthly_cost_usd": monthly_cost,
        "cost_by_tier": {},
        "avg_cost_per_tier": {},
        "success_rate_by_tier": {},
        "avg_attempts_by_tier": {},
        "queue_depth": queued + running,
        "failure_categories": {},
        "recent_issues": recent_issues,
        "top_expensive_issues": top_expensive_issues,
        "cost_by_repo": cost_by_repo,
        "tokens_by_agent": tokens_by_agent,
        "token_totals": token_totals,
    }
