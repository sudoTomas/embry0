"""v2 repo routes — GET /v2/repos and (in B5) GET /v2/repos/{repo}/runs."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from athanor.api.v2.schemas import RepoEntry, RunListItem

router = APIRouter()


@router.get("/repos", response_model=list[RepoEntry])
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


@router.get("/repos/{repo:path}/runs", response_model=list[RunListItem])
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
