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
            "turbo_remote": result.cache_hits.turbo_remote,
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
                        turbo_remote=bool((cache_hits_data or {}).get("turbo_remote", False)),
                    ),
                    trace_url=row["trace_url"],
                    failure_summary=row["failure_summary"],
                    raw_result=raw_result_data or {},
                )
            )
        return out
