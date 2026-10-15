"""Per-repo preferences endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from embry0.api.schemas.repo_preferences import (
    RepoPreferencesResponse,
    RepoPreferencesUpdateRequest,
)

router = APIRouter()


@router.get("/repos/{full_repo:path}/preferences", response_model=RepoPreferencesResponse | None)
async def get_repo_preferences(full_repo: str, request: Request) -> RepoPreferencesResponse | None:
    prefs_repo = request.app.state.repo_preferences_repo
    row = await prefs_repo.get(full_repo)
    if row is None:
        return None
    return RepoPreferencesResponse(**row)


@router.put("/repos/{full_repo:path}/preferences", response_model=RepoPreferencesResponse)
async def put_repo_preferences(
    full_repo: str, req: RepoPreferencesUpdateRequest, request: Request
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

    if req.pr_review is not None and req.pr_review.model:
        from embry0.services.model_enablement import UnpricedModelError, ensure_models_priced

        try:
            ensure_models_priced([req.pr_review.model])
        except UnpricedModelError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    prefs_repo = request.app.state.repo_preferences_repo
    # #350: only the fields the client sent are written; an omitted field
    # keeps its stored value, an explicit null (or "", [] or {}) clears it.
    # exclude_unset also keeps verification {} as {} (= clear) rather than
    # a dict of defaults.
    fields = req.model_dump(exclude_unset=True)
    if req.pr_review is not None:
        # #30: exclude_unset reaches into nested models; the settings are one
        # object and are stored whole, defaults included.
        fields["pr_review"] = req.pr_review.model_dump()
    saved = await prefs_repo.upsert(full_repo, **fields)
    return RepoPreferencesResponse(**saved)


@router.delete("/repos/{full_repo:path}/preferences", status_code=204, response_model=None)
async def delete_repo_preferences(full_repo: str, request: Request) -> None:
    prefs_repo = request.app.state.repo_preferences_repo
    await prefs_repo.delete(full_repo)
