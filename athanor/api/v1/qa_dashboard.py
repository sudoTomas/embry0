"""QA dashboard routes — GET /api/v1/qa/... endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from athanor.api.schemas.qa_dashboard import (
    AffectedSetResponse,
    AppHistoryItem,
    AppResult,
    BootPhaseDetail,
    CacheHitsModel,
    DepEdge,
    RepoEntry,
    RunDetail,
    RunListItem,
)
from athanor.workflows.qa.subtask_result_schema import SubTaskStatus

router = APIRouter()

# Mirrors athanor/workflows/qa/subtask_result_schema.py:77 overall_status().
# Kept here as a free function (not importing the orchestrator's overall_status())
# because that helper consumes SubTaskResult objects whereas the dashboard works
# with QAAppResultRow rows.
_FAILED_DASHBOARD_STATUSES = frozenset(
    {
        SubTaskStatus.QA_FAILURE,
        SubTaskStatus.E2E_FAILURE,
        SubTaskStatus.BOOT_FAILURE,
        SubTaskStatus.READY_CHECK_FAILED,
        SubTaskStatus.INCONCLUSIVE,
    }
)


def _to_app_result(row) -> AppResult:
    boot_phase_obj: BootPhaseDetail | None = None
    bp = getattr(row, "boot_phase", None)
    if isinstance(bp, dict):
        # Defensive: validate via the pydantic model so unexpected fields are
        # rejected with extra="forbid". Older rows pre-Phase-5A have NULL.
        boot_phase_obj = BootPhaseDetail.model_validate(bp)
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
        boot_phase=boot_phase_obj,
    )


def _overall_status(rows) -> str:
    """Three-way rollup mirroring orchestrator's overall_status().

    INFRA_FAILURE short-circuits to 'infra_error' (athanor's fault, not the
    user's). SKIPPED is treated as a pass. Anything else in the failure
    enum returns 'failed'. Default 'passed'.
    """
    if any(r.status == SubTaskStatus.INFRA_FAILURE for r in rows):
        return "infra_error"
    if any(r.status in _FAILED_DASHBOARD_STATUSES for r in rows):
        return "failed"
    return "passed"


@router.get("/qa/repos", response_model=list[RepoEntry])
async def list_repos(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
) -> list[RepoEntry]:
    """List repos that have at least one QA run, latest first."""
    repo = request.app.state.qa_app_results_repo
    rows = await repo.list_repos_with_runs(limit=limit)
    return [
        RepoEntry(
            repo=r.repo,
            latest_run_id=r.latest_run_id,
            latest_status=r.latest_status,
            latest_started_at=r.latest_started_at,
            latest_app_count=r.latest_app_count,
        )
        for r in rows
    ]


@router.get("/qa/repos/{repo:path}/runs", response_model=list[RunListItem])
async def list_runs_for_repo(
    repo: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[RunListItem]:
    """Paginated run list for one repo, newest first."""
    qa_repo = request.app.state.qa_app_results_repo
    rows = await qa_repo.list_runs_for_repo(repo, limit=limit, offset=offset)
    return [
        RunListItem(
            job_id=r.job_id,
            repo=r.repo,
            started_at=r.started_at,
            overall_status=r.overall_status,
            app_count=r.app_count,
        )
        for r in rows
    ]


@router.get(
    "/qa/repos/{repo:path}/apps/{app}/history",
    response_model=list[AppHistoryItem],
)
async def list_app_history(
    repo: str,
    app: str,
    request: Request,
    limit: int = Query(20, ge=1, le=100),
) -> list[AppHistoryItem]:
    """Last-N runs that included this (repo, app) pair, newest first."""
    qa_repo = request.app.state.qa_app_results_repo
    rows = await qa_repo.list_history_for_app(repo, app, limit=limit)
    return [
        AppHistoryItem(
            job_id=r.job_id,
            app_name=r.app_name,
            status=r.status,
            duration_ms=r.duration_ms,
            started_at=r.started_at,
            failure_summary=r.failure_summary,
        )
        for r in rows
    ]


@router.get("/qa/runs/{run_id}", response_model=RunDetail)
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


@router.get("/qa/runs/{run_id}/apps/{app}", response_model=AppResult)
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


@router.get(
    "/qa/runs/{run_id}/affected_set",
    response_model=AffectedSetResponse,
)
async def get_run_affected_set(
    run_id: str,
    request: Request,
) -> AffectedSetResponse:
    """Phase 5D: the run-level affected-set / dep-graph snapshot.

    Returns the row qa_orchestrator_node persisted at fan-out time —
    apps_to_qa, apps_skipped (computed: declared apps not in apps_to_qa),
    force_all_apps, the diff (changed_files + base_branch), and the dep
    graph edges (currently empty list per Phase 5D).

    404 when:
      - the run never wrote metadata (e.g. infra_error before fan-out
        resolution; legacy runs from before Phase 5D)
      - the run_id is unknown
    """
    repo = request.app.state.qa_run_metadata_repo
    md = await repo.get(run_id)
    if md is None:
        raise HTTPException(
            status_code=404,
            detail=f"affected_set not found for run {run_id!r}",
        )
    return AffectedSetResponse(
        job_id=md.job_id,
        apps_to_qa=md.apps_to_qa,
        apps_skipped=md.apps_skipped,
        force_all_apps=md.force_all_apps,
        changed_files=md.changed_files,
        base_branch=md.base_branch,
        dep_graph=[DepEdge(**e) for e in md.dep_graph],
    )
