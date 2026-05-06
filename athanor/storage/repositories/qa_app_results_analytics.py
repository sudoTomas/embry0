"""Aggregator queries on qa_app_results — cache hit ratios + flake detection.

Extracted from ``qa_app_results.py`` to keep that file focused on per-row
CRUD (upsert / list_for_job / list_repos_with_runs / list_runs_for_repo /
list_history_for_app). The repo class delegates here for analytics methods,
so the public ``QAAppResultsRepository`` API is unchanged.

Both functions take the database pool as the first positional arg and the
remaining query params as keyword args. They are independently testable
without instantiating the repo class.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from athanor.storage.database import DatabasePool


async def cache_analytics_window(
    db: DatabasePool,
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
    # cache_hits is a JSONB object — read fields directly with ->> and
    # array entries with jsonb_array_length(... -> ...). Earlier versions
    # of this query went through ``(cache_hits #>> '{}')::jsonb`` to undo
    # an upsert-side double-encode bug; that bug is now fixed (codec
    # encodes once, repo passes Python objects) and a migration recasts
    # legacy rows, so the workaround is gone.
    sql = """
    WITH window_results AS (
        SELECT
            r.app_name,
            (r.cache_hits->>'prebaked_image')::bool AS pi,
            (r.cache_hits->>'shared_volume')::bool AS sv,
            COALESCE(
                jsonb_array_length(r.cache_hits->'turbo_remote_hits'),
                0
            ) AS tr_hits,
            COALESCE(
                jsonb_array_length(r.cache_hits->'turbo_remote_misses'),
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
    rows = await db.fetch(sql, repo, int(days))

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


async def flake_window(
    db: DatabasePool,
    *,
    repo: str,
    days: int = 7,
) -> list[dict[str, Any]]:
    """Phase 5F: per-app flake counts over the last ``days`` days for ``repo``.

    A "flake" is an app whose status differed between consecutive runs on
    the SAME ``head_sha`` within the window. Total flake_count for an app
    is the number of times its status changed back-to-back across runs of
    the same head_sha. Higher values indicate flakier apps.

    SQL approach:
      1. CTE joins ``qa_app_results`` to ``qa_run_metadata`` (for head_sha)
         and ``jobs`` (for repo + created_at) inside the window.
      2. Rows with ``head_sha = ''`` (legacy rows pre-migration 32) are
         excluded — without a SHA we cannot establish "same workspace head".
      3. ``LAG(status) OVER (PARTITION BY app_name, head_sha
                              ORDER BY j.created_at, r.id)`` gives the
         previous status on the same head_sha. The secondary sort on
         ``r.id`` (UUID PK) is a deterministic tie-breaker when two runs
         share a created_at timestamp — without it, ``LAG`` could pick
         either neighbor and the flake count would be non-deterministic.
      4. A row is a "flake event" when ``prev_status IS NOT NULL AND
         prev_status != status``.
      5. Aggregate per ``app_name``: total runs in window + flake count;
         also collect per-day flake counts.

    ``flake_score`` = ``flake_count / max(total_runs, 1)`` clamped to
    [0, 1].

    ``daily`` is filled in Python for every day in the window
    ``[NOW - days, NOW]`` (UTC, calendar-day boundary). Days with zero
    flakes still appear so the heatmap renders a stable grid.

    Returns:
      List of dicts, one per app, sorted by ``flake_score DESC, app_name
      ASC`` so the dashboard's "rows = apps" axis surfaces the worst
      offenders first.
    """
    sql = """
    WITH joined AS (
        SELECT
            r.app_name,
            r.status,
            m.head_sha,
            j.created_at,
            r.id
        FROM qa_app_results r
        JOIN qa_run_metadata m ON m.job_id = r.job_id
        JOIN jobs j ON j.job_id = r.job_id
        WHERE j.repo = $1
          AND j.created_at >= NOW() - ($2::int * INTERVAL '1 day')
          AND m.head_sha <> ''
    ),
    with_prev AS (
        SELECT
            app_name,
            status,
            head_sha,
            created_at,
            LAG(status) OVER (
                PARTITION BY app_name, head_sha
                ORDER BY created_at ASC, id ASC
            ) AS prev_status
        FROM joined
    ),
    flake_events AS (
        SELECT
            app_name,
            created_at,
            CASE WHEN prev_status IS NOT NULL AND prev_status <> status
                 THEN 1 ELSE 0 END AS is_flake
        FROM with_prev
    )
    SELECT
        app_name,
        COUNT(*)::int AS total_runs,
        COALESCE(SUM(is_flake), 0)::int AS flake_count,
        COALESCE(
            ARRAY_AGG(created_at ORDER BY created_at)
                FILTER (WHERE is_flake = 1),
            ARRAY[]::timestamptz[]
        ) AS flake_timestamps
    FROM flake_events
    GROUP BY app_name
    ORDER BY app_name
    """
    rows = await db.fetch(sql, repo, int(days))

    # Build the canonical day grid for the window so every app's `daily`
    # array has identical keys — keeps the heatmap CSS grid stable
    # regardless of which days produced flakes.
    today = datetime.now(UTC).date()
    # Window is INCLUSIVE of today and goes back ``days`` calendar days
    # so day count = ``days``. With days=7, the grid spans the past week
    # (today, today-1, …, today-6).
    day_keys: list[str] = [
        (today - timedelta(days=offset)).isoformat()
        for offset in range(int(days) - 1, -1, -1)
    ] if int(days) > 0 else []

    out: list[dict[str, Any]] = []
    for r in rows:
        total_runs = int(r["total_runs"])
        flake_count = int(r["flake_count"])
        flake_score = (flake_count / total_runs) if total_runs > 0 else 0.0
        # Defensive clamp — flake_count <= total_runs by construction
        # (LAG produces at most total_runs - 1 events) but the float
        # division can produce 0.999... so we clamp to be explicit.
        flake_score = max(0.0, min(1.0, flake_score))

        # Bucket flake timestamps into UTC day keys.
        per_day: dict[str, int] = {key: 0 for key in day_keys}
        for ts in (r["flake_timestamps"] or []):
            key = ts.astimezone(UTC).date().isoformat()
            if key in per_day:
                per_day[key] += 1
            # Outside-window timestamps shouldn't happen (the CTE filters
            # by created_at) but if they slip in (clock skew / DST edge),
            # silently drop them rather than crash the heatmap.

        daily = [{"date": key, "flakes": per_day[key]} for key in day_keys]

        out.append(
            {
                "app_name": r["app_name"],
                "total_runs": total_runs,
                "flake_count": flake_count,
                "flake_score": flake_score,
                "daily": daily,
            }
        )

    # Sort: flakier apps first, then alpha for stability.
    out.sort(key=lambda d: (-d["flake_score"], d["app_name"]))
    return out
