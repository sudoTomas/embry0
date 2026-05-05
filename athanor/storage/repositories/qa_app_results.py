"""Repository for qa_app_results — per-app QA outcomes for multi-app runs.

Joined to the existing `jobs` table via job_id (TEXT). Composite uniqueness
on (job_id, app_name) so retries replace rows rather than duplicate. The
upsert is the only mutating operation; sub-task subgraph nodes call it once
per sub-task at orchestrator-aggregation time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from athanor.storage.database import DatabasePool
from athanor.workflows.qa.subtask_result_schema import (
    CacheHits,
    SubTaskResult,
    SubTaskStatus,
)


@dataclass(frozen=True, slots=True)
class QAAppResultRow:
    job_id: str
    app_name: str
    status: SubTaskStatus
    duration_ms: int
    cache_hits: CacheHits
    trace_url: str | None
    failure_summary: str | None
    raw_result: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RepoSummary:
    """One row from list_repos_with_runs — repo + its latest run."""

    repo: str
    latest_run_id: str
    latest_status: str  # 'passed' | 'failed' | 'mixed' (computed)
    latest_started_at: Any  # datetime; Any avoids importing datetime in __all__
    latest_app_count: int


@dataclass(frozen=True, slots=True)
class RunSummary:
    """One row from list_runs_for_repo — a run's high-level metadata."""

    job_id: str
    repo: str
    started_at: Any  # datetime
    overall_status: str  # 'passed' | 'failed'
    app_count: int


@dataclass(frozen=True, slots=True)
class AppHistoryRow:
    """One row from list_history_for_app — a per-(job, app) historical entry."""

    job_id: str
    app_name: str
    status: str  # SubTaskStatus value
    duration_ms: int
    started_at: Any  # datetime
    failure_summary: str | None


class QAAppResultsRepository:
    def __init__(self, db: DatabasePool) -> None:
        self.db = db

    async def upsert(self, job_id: str, result: SubTaskResult) -> None:
        """Insert or replace the row for (job_id, app_name).

        Idempotent: invariant 2 from spec §9.5 — sub-task retries REPLACE
        rows, never duplicate. The unique index uq_qa_app_results_job_app
        gives the ON CONFLICT target.
        """
        cache_hits_json = json.dumps({
            "prebaked_image": result.cache_hits.prebaked_image,
            "shared_volume": result.cache_hits.shared_volume,
            "turbo_remote_hits": list(result.cache_hits.turbo_remote_hits),
            "turbo_remote_misses": list(result.cache_hits.turbo_remote_misses),
        })
        sql = """
        INSERT INTO qa_app_results (
            job_id, app_name, status, duration_ms, cache_hits,
            trace_url, failure_summary, raw_result, updated_at
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8::jsonb, NOW())
        ON CONFLICT (job_id, app_name) DO UPDATE SET
            status          = EXCLUDED.status,
            duration_ms     = EXCLUDED.duration_ms,
            cache_hits      = EXCLUDED.cache_hits,
            trace_url       = EXCLUDED.trace_url,
            failure_summary = EXCLUDED.failure_summary,
            raw_result      = EXCLUDED.raw_result,
            updated_at      = NOW()
        """
        await self.db.execute(
            sql,
            job_id,
            result.app_name,
            str(result.status),
            int(result.duration_ms),
            cache_hits_json,
            result.trace_url,
            result.failure_summary,
            json.dumps(result.raw_result),
        )

    async def list_for_job(self, job_id: str) -> list[QAAppResultRow]:
        """Return all per-app rows for one job, sorted by app_name.

        Used by the orchestrator's report node to render the sticky PR comment
        and the aggregate GitHub check (Tasks 23-25) and by the dashboard
        in Phase 4.
        """
        sql = """
        SELECT job_id, app_name, status, duration_ms, cache_hits,
               trace_url, failure_summary, raw_result
        FROM qa_app_results
        WHERE job_id = $1
        ORDER BY app_name
        """
        rows = await self.db.fetch(sql, job_id)

        out: list[QAAppResultRow] = []
        for row in rows:
            cache_hits_raw = row["cache_hits"]
            cache_hits_data = (
                json.loads(cache_hits_raw) if isinstance(cache_hits_raw, str) else cache_hits_raw
            )
            raw_result_raw = row["raw_result"]
            raw_result_data = (
                json.loads(raw_result_raw) if isinstance(raw_result_raw, str) else raw_result_raw
            )

            out.append(
                QAAppResultRow(
                    job_id=row["job_id"],
                    app_name=row["app_name"],
                    status=SubTaskStatus(row["status"]),
                    duration_ms=row["duration_ms"],
                    cache_hits=CacheHits(
                        prebaked_image=bool((cache_hits_data or {}).get("prebaked_image", False)),
                        shared_volume=bool((cache_hits_data or {}).get("shared_volume", False)),
                        # Backward-compat: old rows had turbo_remote: bool — ignore that field.
                        turbo_remote_hits=list((cache_hits_data or {}).get("turbo_remote_hits") or []),
                        turbo_remote_misses=list((cache_hits_data or {}).get("turbo_remote_misses") or []),
                    ),
                    trace_url=row["trace_url"],
                    failure_summary=row["failure_summary"],
                    raw_result=raw_result_data or {},
                )
            )
        return out

    async def list_repos_with_runs(self, limit: int = 50) -> list[RepoSummary]:
        """Distinct repos that have at least one qa_app_results row.

        For each repo, returns the latest job_id (by started_at), its overall
        status (passed if every per-app row passed; failed otherwise), and
        the number of apps in that latest run.
        """
        sql = """
        WITH latest_per_repo AS (
            SELECT
                j.repo,
                j.job_id,
                j.started_at,
                ROW_NUMBER() OVER (PARTITION BY j.repo ORDER BY j.started_at DESC) AS rn
            FROM jobs j
            WHERE EXISTS (SELECT 1 FROM qa_app_results q WHERE q.job_id = j.job_id)
        )
        SELECT
            l.repo,
            l.job_id AS latest_run_id,
            l.started_at AS latest_started_at,
            COUNT(q.id) AS latest_app_count,
            CASE WHEN COUNT(*) FILTER (WHERE q.status != 'passed') > 0 THEN 'failed' ELSE 'passed' END AS latest_status
        FROM latest_per_repo l
        JOIN qa_app_results q ON q.job_id = l.job_id
        WHERE l.rn = 1
        GROUP BY l.repo, l.job_id, l.started_at
        ORDER BY l.started_at DESC NULLS LAST
        LIMIT $1
        """
        rows = await self.db.fetch(sql, limit)
        return [
            RepoSummary(
                repo=r["repo"],
                latest_run_id=r["latest_run_id"],
                latest_status=r["latest_status"],
                latest_started_at=r["latest_started_at"],
                latest_app_count=int(r["latest_app_count"]),
            )
            for r in rows
        ]

    async def list_runs_for_repo(
        self,
        repo: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RunSummary]:
        """Recent runs for a repo, newest first. Each row aggregates per-app
        results into an overall status."""
        sql = """
        SELECT
            j.job_id,
            j.repo,
            j.started_at,
            COUNT(q.id) AS app_count,
            CASE WHEN COUNT(*) FILTER (WHERE q.status != 'passed') > 0 THEN 'failed' ELSE 'passed' END AS overall_status
        FROM jobs j
        JOIN qa_app_results q ON q.job_id = j.job_id
        WHERE j.repo = $1
        GROUP BY j.job_id, j.repo, j.started_at
        ORDER BY j.started_at DESC NULLS LAST
        LIMIT $2 OFFSET $3
        """
        rows = await self.db.fetch(sql, repo, limit, offset)
        return [
            RunSummary(
                job_id=r["job_id"],
                repo=r["repo"],
                started_at=r["started_at"],
                overall_status=r["overall_status"],
                app_count=int(r["app_count"]),
            )
            for r in rows
        ]

    async def list_history_for_app(
        self,
        repo: str,
        app_name: str,
        *,
        limit: int = 20,
    ) -> list[AppHistoryRow]:
        """Last-N qa_app_results rows for one (repo, app_name) pair, newest first."""
        sql = """
        SELECT
            q.job_id,
            q.app_name,
            q.status,
            q.duration_ms,
            j.started_at,
            q.failure_summary
        FROM qa_app_results q
        JOIN jobs j ON j.job_id = q.job_id
        WHERE j.repo = $1 AND q.app_name = $2
        ORDER BY j.started_at DESC NULLS LAST
        LIMIT $3
        """
        rows = await self.db.fetch(sql, repo, app_name, limit)
        return [
            AppHistoryRow(
                job_id=r["job_id"],
                app_name=r["app_name"],
                status=r["status"],
                duration_ms=int(r["duration_ms"]),
                started_at=r["started_at"],
                failure_summary=r["failure_summary"],
            )
            for r in rows
        ]
