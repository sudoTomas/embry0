"""Per-repo preferences endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from embry0.api.schemas.repo_preferences import (
    RepoPreferencesResponse,
    RepoPreferencesUpdateRequest,
)

router = APIRouter()


@router.get("/repos/{owner}/{repo}/preferences", response_model=RepoPreferencesResponse | None)
async def get_repo_preferences(owner: str, repo: str, request: Request) -> RepoPreferencesResponse | None:
    prefs_repo = request.app.state.repo_preferences_repo
    row = await prefs_repo.get(f"{owner}/{repo}")
    if row is None:
        return None
    return RepoPreferencesResponse(**row)


@router.put("/repos/{owner}/{repo}/preferences", response_model=RepoPreferencesResponse)
async def put_repo_preferences(
    owner: str, repo: str, req: RepoPreferencesUpdateRequest, request: Request
) -> RepoPreferencesResponse:
    # Validate sandbox_profile exists if provided. The orchestrator attaches
    # the repository as ``profiles_repo`` on app.state (see embry0/api/app.py).
    if req.sandbox_profile:
        profiles_repo = getattr(request.app.state, "profiles_repo", None)
        if profiles_repo is not None:
            row = await profiles_repo.get(req.sandbox_profile)
            if row is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"sandbox_profile '{req.sandbox_profile}' does not exist",
                )

    prefs_repo = request.app.state.repo_preferences_repo
    saved = await prefs_repo.upsert(
        repo=f"{owner}/{repo}",
        sandbox_profile=req.sandbox_profile,
        language_hint=req.language_hint,
        notes=req.notes,
        execution_mode=req.execution_mode,
        auth_mode=req.auth_mode,
        git_author_name=req.git_author_name,
        git_author_email=req.git_author_email,
    )
    return RepoPreferencesResponse(**saved)


@router.delete("/repos/{owner}/{repo}/preferences", status_code=204, response_model=None)
async def delete_repo_preferences(owner: str, repo: str, request: Request) -> None:
    prefs_repo = request.app.state.repo_preferences_repo
    await prefs_repo.delete(f"{owner}/{repo}")
