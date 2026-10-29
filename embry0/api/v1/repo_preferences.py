"""Per-repo preferences endpoints.

Both writes are recorded in the repo's history (ADR 0019) and audited.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from embry0.api.deps import request_actor
from embry0.api.schemas.repo_preferences import (
    RepoPreferencesResponse,
    RepoPreferencesUpdateRequest,
)
from embry0.audit.actions import AuditAction
from embry0.audit.helpers import emit_audit

router = APIRouter()


async def _audit(request: Request, action: AuditAction, details: dict[str, Any]) -> None:
    config = getattr(request.app.state, "config", None)
    await emit_audit(
        getattr(request.app.state, "db", None),
        action,
        actor=request_actor(request),
        details=details,
        audit_log_path=getattr(config, "audit_log_path", None),
    )


@router.get("/repos/{full_repo:repo}/preferences", response_model=RepoPreferencesResponse | None)
async def get_repo_preferences(full_repo: str, request: Request) -> RepoPreferencesResponse | None:
    prefs_repo = request.app.state.repo_preferences_repo
    row = await prefs_repo.get(full_repo)
    if row is None:
        return None
    pr_review = row.get("pr_review")
    if isinstance(pr_review, dict) and "ready_authors" not in pr_review:
        # #30: settings saved before ready_authors existed are read as the
        # team default everywhere (review_settings), so say so here too.
        row = {**row, "pr_review": {**pr_review, "ready_authors": "team"}}
    return RepoPreferencesResponse(**row)


@router.put("/repos/{full_repo:repo}/preferences", response_model=RepoPreferencesResponse)
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
    saved, revision_id = await prefs_repo.upsert_recorded(full_repo, actor=request_actor(request), **fields)
    await _audit(
        request, "repo.preferences_set", {"repo": full_repo, "revision_id": revision_id, "fields": sorted(fields)}
    )
    # The revision id stays out of the response model; history serves it.
    return RepoPreferencesResponse(**saved)


@router.delete("/repos/{full_repo:repo}/preferences", status_code=204, response_model=None)
async def delete_repo_preferences(full_repo: str, request: Request) -> None:
    prefs_repo = request.app.state.repo_preferences_repo
    revision_id = await prefs_repo.delete_recorded(full_repo, actor=request_actor(request))
    if revision_id is not None:
        await _audit(request, "repo.preferences_cleared", {"repo": full_repo, "revision_id": revision_id})
