"""Repository for qa_app_results — per-app QA outcomes for multi-app runs.

Joined to the existing `jobs` table via job_id (TEXT). Composite uniqueness
on (job_id, app_name) so retries replace rows rather than duplicate. The
upsert is the only mutating operation; sub-task subgraph nodes call it once
per sub-task at orchestrator-aggregation time.
"""

from __future__ import annotations

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
    # Phase 5A: per-sub-task boot drill-down. None on legacy rows and on the
    # passed path. JSONB column on qa_app_results, parsed back into a dict
    # here. Shape mirrors athanor.api.schemas.qa_dashboard.BootPhaseDetail.
    boot_phase: dict[str, Any] | None = None


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

        Forwards to ``upsert_with_boot_phase`` so both paths share one SQL
        statement and the boot_phase column is always written deterministically
        (NULL for SubTaskResult.boot_phase=None).
        """
        await self.upsert_with_boot_phase(
            job_id=job_id,
            app_name=result.app_name,
            status=str(result.status),
            duration_ms=int(result.duration_ms),
            cache_hits=result.cache_hits,
            trace_url=result.trace_url,
            failure_summary=result.failure_summary,
            raw_result=result.raw_result,
            boot_phase=result.boot_phase,
        )

    async def upsert_with_boot_phase(
        self,
        *,
        job_id: str,
        app_name: str,
        status: str,
        duration_ms: int,
        cache_hits: CacheHits,
        trace_url: str | None = None,
        failure_summary: str | None = None,
        raw_result: dict[str, Any] | None = None,
        boot_phase: dict[str, Any] | None = None,
    ) -> None:
        """Idempotent upsert that also writes the boot_phase JSONB column.

        ``boot_phase=None`` writes SQL NULL (not the JSON string ``"null"``)
        — the column is intentionally nullable so legacy rows and the
        passed-boot path are distinguishable from a captured boot_phase
        with all-empty fields.

        JSONB params (cache_hits, raw_result, boot_phase) are passed as Python
        objects — the asyncpg JSONB codec handles encoding (see
        ``athanor/storage/database.py``). Pre-encoding with ``json.dumps`` here
        would double-wrap the values as JSON-string-scalars inside the JSONB
        column.
        """
        cache_hits_payload = {
            "prebaked_image": cache_hits.prebaked_image,
            "shared_volume": cache_hits.shared_volume,
            "turbo_remote_hits": list(cache_hits.turbo_remote_hits),
            "turbo_remote_misses": list(cache_hits.turbo_remote_misses),
        }
        sql = """
        INSERT INTO qa_app_results (
            job_id, app_name, status, duration_ms, cache_hits,
            trace_url, failure_summary, raw_result, boot_phase, updated_at
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8::jsonb, $9::jsonb, NOW())
        ON CONFLICT (job_id, app_name) DO UPDATE SET
            status          = EXCLUDED.status,
            duration_ms     = EXCLUDED.duration_ms,
            cache_hits      = EXCLUDED.cache_hits,
            trace_url       = EXCLUDED.trace_url,
            failure_summary = EXCLUDED.failure_summary,
            raw_result      = EXCLUDED.raw_result,
            boot_phase      = EXCLUDED.boot_phase,
            updated_at      = NOW()
        """
        await self.db.execute(
            sql,
            job_id,
            app_name,
            status,
            int(duration_ms),
            cache_hits_payload,
            trace_url,
            failure_summary,
            raw_result or {},
            boot_phase,
        )

    async def list_for_job(self, job_id: str) -> list[QAAppResultRow]:
        """Return all per-app rows for one job, sorted by app_name.

        Used by the orchestrator's report node to render the sticky PR comment
        and the aggregate GitHub check (Tasks 23-25) and by the dashboard
        in Phase 4.
        """
        sql = """
        SELECT job_id, app_name, status, duration_ms, cache_hits,
               trace_url, failure_summary, raw_result, boot_phase
        FROM qa_app_results
        WHERE job_id = $1
        ORDER BY app_name
        """
        rows = await self.db.fetch(sql, job_id)

        out: list[QAAppResultRow] = []
        for row in rows:
            # JSONB columns come back as Python objects via the asyncpg codec
            # (see athanor/storage/database.py). No json.loads here.
            cache_hits_data = row["cache_hits"] or {}
            raw_result_data = row["raw_result"] or {}
            boot_phase_data = row["boot_phase"] if "boot_phase" in row.keys() else None

            out.append(
                QAAppResultRow(
                    job_id=row["job_id"],
                    app_name=row["app_name"],
                    status=SubTaskStatus(row["status"]),
                    duration_ms=row["duration_ms"],
                    cache_hits=CacheHits(
                        prebaked_image=bool(cache_hits_data.get("prebaked_image", False)),
                        shared_volume=bool(cache_hits_data.get("shared_volume", False)),
                        # Backward-compat: old rows had turbo_remote: bool — ignore that field.
                        turbo_remote_hits=list(cache_hits_data.get("turbo_remote_hits") or []),
                        turbo_remote_misses=list(cache_hits_data.get("turbo_remote_misses") or []),
                    ),
                    trace_url=row["trace_url"],
                    failure_summary=row["failure_summary"],
                    raw_result=raw_result_data,
                    boot_phase=boot_phase_data,
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
            CASE
              WHEN COUNT(*) FILTER (WHERE q.status = 'infra_failure') > 0 THEN 'infra_error'
              WHEN COUNT(*) FILTER (WHERE q.status IN ('qa_failure','e2e_failure','boot_failure','ready_check_failed','inconclusive')) > 0 THEN 'failed'
              ELSE 'passed'
            END AS latest_status
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
            CASE
              WHEN COUNT(*) FILTER (WHERE q.status = 'infra_failure') > 0 THEN 'infra_error'
              WHEN COUNT(*) FILTER (WHERE q.status IN ('qa_failure','e2e_failure','boot_failure','ready_check_failed','inconclusive')) > 0 THEN 'failed'
              ELSE 'passed'
            END AS overall_status
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

    async def cache_analytics_window(
        self,
        *,
        repo: str,
        days: int = 30,
    ) -> dict[str, Any]:
        """Phase 5E: aggregate per-layer cache hit/miss for a repo over a window.

        Returns the payload for the
        ``GET /api/v1/qa/repos/{repo}/cache/analytics`` endpoint. Joins
        ``qa_app_results`` to ``jobs`` on ``job_id`` (no SUBSTRING needed —
        ``qa_app_results.job_id`` is the parent job_id, mirroring how
        ``list_runs_for_repo`` joins). Filtered by ``j.created_at`` so
        queued-but-never-started jobs still count toward the window.

        Per layer:
          - prebaked_image / shared_volume: ``cache_hits.<layer>`` is a bool;
            it becomes 1 hit OR 1 miss per row.
          - turbo_remote: ``cache_hits.turbo_remote_hits`` and
            ``turbo_remote_misses`` are list[str] — every list element is one
            hit / miss event (turbo runs many tasks per sub-task).

        ``cold_cache_apps`` are apps whose aggregate hit_ratio (across all
        three layers, weighted by event count) falls below 0.25 AND whose
        sub-task count is at least 3 (so a single bad run doesn't poison
        the list). Returned alphabetically.
        """
        # NOTE on the (cache_hits #>> '{}')::jsonb dance: the database codec
        # in DatabasePool.connect() registers a jsonb encoder that calls
        # json.dumps() on every Python value. Combined with the
        # ``$5::jsonb`` cast and ``json.dumps(...)`` in upsert_with_boot_phase,
        # cache_hits ends up wrapped as a JSON-string-scalar inside the JSONB
        # column (i.e. the cell holds ``"\"{...}\""`` rather than ``{...}``).
        # Reads via list_for_job paper over this with json.loads on the
        # Python side, but server-side ``->>`` and ``->`` operators see a
        # string scalar and return NULL. ``#>> '{}'`` extracts the JSON value
        # as text; re-casting to ::jsonb gives us the inner object so the
        # JSONB operators work.
        sql = """
        WITH window_results AS (
            SELECT
                r.app_name,
                (((r.cache_hits #>> '{}')::jsonb)->>'prebaked_image')::bool AS pi,
                (((r.cache_hits #>> '{}')::jsonb)->>'shared_volume')::bool AS sv,
                COALESCE(
                    jsonb_array_length(((r.cache_hits #>> '{}')::jsonb)->'turbo_remote_hits'),
                    0
                ) AS tr_hits,
                COALESCE(
                    jsonb_array_length(((r.cache_hits #>> '{}')::jsonb)->'turbo_remote_misses'),
                    0
                ) AS tr_misses,
                r.job_id AS parent_job_id
            FROM qa_app_results r
            JOIN jobs j ON j.job_id = r.job_id
            WHERE j.repo = $1
              AND j.created_at >= NOW() - ($2::int * INTERVAL '1 day')
        )
        SELECT
            app_name,
            COUNT(*)::int AS subtasks,
            (COUNT(*) FILTER (WHERE pi))::int AS pi_hits,
            (COUNT(*) FILTER (WHERE NOT pi))::int AS pi_misses,
            (COUNT(*) FILTER (WHERE sv))::int AS sv_hits,
            (COUNT(*) FILTER (WHERE NOT sv))::int AS sv_misses,
            COALESCE(SUM(tr_hits), 0)::int AS tr_hits,
            COALESCE(SUM(tr_misses), 0)::int AS tr_misses,
            (SELECT COUNT(DISTINCT parent_job_id) FROM window_results)::int AS total_runs
        FROM window_results
        GROUP BY app_name
        """
        rows = await self.db.fetch(sql, repo, int(days))

        # Empty window: short-circuit with the canonical zero payload so
        # the API response shape is always predictable.
        if not rows:
            return {
                "repo": repo,
                "window_days": int(days),
                "total_runs": 0,
                "total_subtasks": 0,
                "layers": [
                    {"layer": "prebaked_image", "hits": 0, "misses": 0, "hit_ratio": 0.0},
                    {"layer": "shared_volume", "hits": 0, "misses": 0, "hit_ratio": 0.0},
                    {"layer": "turbo_remote", "hits": 0, "misses": 0, "hit_ratio": 0.0},
                ],
                "cold_cache_apps": [],
            }

        # Roll up per-layer totals across all apps, and compute per-app cold
        # ratios for the offenders list.
        total_subtasks = 0
        total_runs = int(rows[0]["total_runs"])  # same value on every row
        pi_hits_sum = sv_hits_sum = tr_hits_sum = 0
        pi_misses_sum = sv_misses_sum = tr_misses_sum = 0

        cold_cache_apps: list[str] = []
        for r in rows:
            subtasks = int(r["subtasks"])
            total_subtasks += subtasks

            pi_hits = int(r["pi_hits"])
            pi_misses = int(r["pi_misses"])
            sv_hits = int(r["sv_hits"])
            sv_misses = int(r["sv_misses"])
            tr_hits = int(r["tr_hits"])
            tr_misses = int(r["tr_misses"])

            pi_hits_sum += pi_hits
            pi_misses_sum += pi_misses
            sv_hits_sum += sv_hits
            sv_misses_sum += sv_misses
            tr_hits_sum += tr_hits
            tr_misses_sum += tr_misses

            # Per-app aggregate hit_ratio across all three layers, weighted by
            # event count (so a layer with 100 turbo events outweighs a single
            # prebaked bool). Apps with zero events are not "cold" — they
            # simply produced no cache data — so we exclude ratio=0 with no
            # events from the offenders list by guarding on total_events > 0.
            app_hits = pi_hits + sv_hits + tr_hits
            app_events = (
                pi_hits + pi_misses
                + sv_hits + sv_misses
                + tr_hits + tr_misses
            )
            if subtasks >= 3 and app_events > 0:
                ratio = app_hits / app_events
                if ratio < 0.25:
                    cold_cache_apps.append(r["app_name"])

        cold_cache_apps.sort()

        def _ratio(hits: int, misses: int) -> float:
            denom = hits + misses
            return hits / denom if denom > 0 else 0.0

        return {
            "repo": repo,
            "window_days": int(days),
            "total_runs": total_runs,
            "total_subtasks": total_subtasks,
            "layers": [
                {
                    "layer": "prebaked_image",
                    "hits": pi_hits_sum,
                    "misses": pi_misses_sum,
                    "hit_ratio": _ratio(pi_hits_sum, pi_misses_sum),
                },
                {
                    "layer": "shared_volume",
                    "hits": sv_hits_sum,
                    "misses": sv_misses_sum,
                    "hit_ratio": _ratio(sv_hits_sum, sv_misses_sum),
                },
                {
                    "layer": "turbo_remote",
                    "hits": tr_hits_sum,
                    "misses": tr_misses_sum,
                    "hit_ratio": _ratio(tr_hits_sum, tr_misses_sum),
                },
            ],
            "cold_cache_apps": cold_cache_apps,
        }

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
