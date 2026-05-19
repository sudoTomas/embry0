"""Admin endpoints for QA dashboard configuration overrides.

Phase 5G: per-repo workspace_provider overrides. Operators set
``(provider_type, config)`` rows here that the orchestrator prefers over
``.athanor/qa.yaml`` when present. See
``athanor.workflows.qa.orchestrator._qa_orchestrator_node_impl`` for the
read-side wiring and
``athanor.storage.repositories.qa_workspace_provider_overrides`` for the
storage layer.

Mounted under the same Bearer-auth dependency as the rest of the
``/qa/...`` surface.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from athanor.api.schemas.qa_dashboard import (
    WorkspaceProviderOverride,
    WorkspaceProviderOverrideUpsert,
)
from athanor.storage.repositories.qa_workspace_provider_overrides import (
    WorkspaceProviderOverride as WorkspaceProviderOverrideRow,
)

router = APIRouter()


def _to_response(row: WorkspaceProviderOverrideRow) -> WorkspaceProviderOverride:
    return WorkspaceProviderOverride(
        repo=row.repo,
        provider_type=row.provider_type,
        config=row.config,
        updated_at=row.updated_at,
    )


@router.get(
    "/qa/admin/providers",
    response_model=list[WorkspaceProviderOverride],
)
async def list_provider_overrides(
    request: Request,
) -> list[WorkspaceProviderOverride]:
    """List every repo with a stored provider override, sorted by repo."""
    repo_db = request.app.state.qa_workspace_provider_overrides_repo
    rows = await repo_db.list_all()
    return [_to_response(r) for r in rows]


@router.get(
    "/qa/admin/providers/{repo:path}",
    response_model=WorkspaceProviderOverride,
)
async def get_provider_override(
    repo: str, request: Request
) -> WorkspaceProviderOverride:
    """Fetch the override for ``repo``, or 404 if no row exists."""
    repo_db = request.app.state.qa_workspace_provider_overrides_repo
    row = await repo_db.get(repo)
    if row is None:
        raise HTTPException(status_code=404, detail="no override for this repo")
    return _to_response(row)


@router.post(
    "/qa/admin/providers/{repo:path}",
    response_model=WorkspaceProviderOverride,
    status_code=200,
)
async def upsert_provider_override(
    repo: str,
    body: WorkspaceProviderOverrideUpsert,
    request: Request,
) -> WorkspaceProviderOverride:
    """Create or replace the override row for ``repo``.

    Returns the post-write row so the client can sync its UI without
    re-fetching the list.
    """
    repo_db = request.app.state.qa_workspace_provider_overrides_repo
    await repo_db.upsert(
        repo=repo,
        provider_type=body.provider_type,
        config=body.config,
    )
    row = await repo_db.get(repo)
    if row is None:
        # Should never happen — we just upserted. Treat as a 500 so any
        # storage-side bug isn't masked as a missing-override 404.
        raise HTTPException(
            status_code=500,
            detail="upsert succeeded but row could not be re-read",
        )
    return _to_response(row)


@router.delete("/qa/admin/providers/{repo:path}", status_code=204)
async def delete_provider_override(repo: str, request: Request) -> None:
    """Drop the override row for ``repo``. Returns 404 when no row existed."""
    repo_db = request.app.state.qa_workspace_provider_overrides_repo
    deleted = await repo_db.delete(repo)
    if not deleted:
        raise HTTPException(status_code=404, detail="no override for this repo")
