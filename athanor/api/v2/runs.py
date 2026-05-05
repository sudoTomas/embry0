"""v2 run routes — GET /v2/runs/{run_id} (and in B5 /apps/{app})."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from athanor.api.v2.schemas import (
    AppResult,
    CacheHitsModel,
    RunDetail,
)

router = APIRouter()


def _to_app_result(row) -> AppResult:
    return AppResult(
        app_name=row.app_name,
        status=str(row.status),
        duration_ms=row.duration_ms,
        cache_hits=CacheHitsModel(
            prebaked_image=row.cache_hits.prebaked_image,
            shared_volume=row.cache_hits.shared_volume,
            turbo_remote_hits=list(row.cache_hits.turbo_remote_hits),
            turbo_remote_misses=list(row.cache_hits.turbo_remote_misses),
        ),
        trace_url=row.trace_url,
        failure_summary=row.failure_summary,
    )


def _overall_status(rows) -> str:
    return "failed" if any(str(r.status) != "passed" for r in rows) else "passed"


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: str, request: Request) -> RunDetail:
    """Full run detail with per-app results inlined."""
    qa_repo = request.app.state.qa_app_results_repo
    jobs_repo = request.app.state.jobs_repo

    job = await jobs_repo.get(run_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")

    rows = await qa_repo.list_for_job(run_id)
    if not rows:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} has no QA app results")

    return RunDetail(
        job_id=run_id,
        repo=job["repo"],
        started_at=job["started_at"],
        overall_status=_overall_status(rows),
        apps=[_to_app_result(r) for r in rows],
    )


@router.get("/runs/{run_id}/apps/{app}", response_model=AppResult)
async def get_run_app(run_id: str, app: str, request: Request) -> AppResult:
    """One per-app result inside a run."""
    qa_repo = request.app.state.qa_app_results_repo
    rows = await qa_repo.list_for_job(run_id)
    for r in rows:
        if r.app_name == app:
            return _to_app_result(r)
    raise HTTPException(
        status_code=404,
        detail=f"run {run_id!r} has no result for app {app!r}",
    )
