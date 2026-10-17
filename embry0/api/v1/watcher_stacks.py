"""Watcher stacks API (#378): what embry0 watches, editable without a redeploy.

Reads are operator-tier like the rest of the watcher API; writes are
admin-tier, because adding a stack points embry0's log analysis — and the
spend behind it — at another deployment.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from embry0.api.deps import request_actor, require_admin
from embry0.audit.helpers import emit_audit
from embry0.services.watcher_stacks import OVERRIDABLE, WatcherStack
from embry0.storage.repositories.watcher_stacks import WatcherStacksRepository

router = APIRouter()


class StackBody(BaseModel):
    logql: str = Field(min_length=1, description="the stack's Loki selector")
    tracker: str = Field(default="linear", pattern="^(linear|github|gitlab)$")
    tracker_target: dict[str, Any] = Field(
        default_factory=dict,
        description='linear: {"team_id", "project_id"}; github/gitlab file into the stack repo itself',
    )
    settings: dict[str, Any] = Field(
        default_factory=dict, description="overrides only; an absent key inherits the global default"
    )
    enabled: bool = True


def _repo(request: Request) -> WatcherStacksRepository:
    # Duck-typed on purpose: an isinstance gate here would 503 on any
    # stand-in, which is how the first version of this route failed its
    # own tests.
    repository = getattr(request.app.state, "watcher_stacks_repo", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="watcher storage not initialized")
    return cast("WatcherStacksRepository", repository)


async def save_stack(
    request: Request, stack: WatcherStack, *, ignored_settings: list[str] | None = None
) -> WatcherStack:
    """Store ``stack`` and write its ``watcher_stack_updated`` audit row.

    The one write path for a stack: ``PUT /watcher/stacks/{owner}/{name}``
    and the organization watch action both store through it. A stack the
    table refuses is a 422.
    """
    try:
        stored = await _repo(request).upsert(stack)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    config = request.app.state.config
    await emit_audit(
        getattr(request.app.state, "db", None),
        "watcher_stack_updated",
        actor=request_actor(request),
        details={
            "repo": stack.repo,
            "tracker": stored.tracker,
            "enabled": stored.enabled,
            "ignored_settings": list(ignored_settings or []),
        },
        audit_log_path=getattr(config, "audit_log_path", None),
    )
    return stored


@router.get("/watcher/stacks")
async def list_stacks(request: Request, enabled_only: bool = False) -> dict[str, Any]:
    stacks = await _repo(request).list_all(enabled_only=enabled_only)
    return {"stacks": [s.to_dict() for s in stacks], "overridable_settings": sorted(OVERRIDABLE)}


@router.get("/watcher/stacks/{owner}/{name}")
async def get_stack(owner: str, name: str, request: Request) -> dict[str, Any]:
    stack = await _repo(request).get(f"{owner}/{name}")
    if stack is None:
        raise HTTPException(status_code=404, detail=f"no watched stack {owner}/{name}")
    return stack.to_dict()


@router.put("/watcher/stacks/{owner}/{name}", dependencies=[require_admin])
async def put_stack(owner: str, name: str, body: StackBody, request: Request) -> dict[str, Any]:
    """Create or replace a watched stack. Unknown ``settings`` keys are
    dropped rather than stored — the response shows what was kept."""
    repo = f"{owner}/{name}"
    unknown = sorted(set(body.settings) - set(OVERRIDABLE))
    stored = await save_stack(
        request,
        WatcherStack(
            repo=repo,
            logql=body.logql,
            tracker=body.tracker,
            tracker_target=body.tracker_target,
            settings=body.settings,
            enabled=body.enabled,
        ),
        ignored_settings=unknown,
    )
    return {**stored.to_dict(), "ignored_settings": unknown}


@router.delete("/watcher/stacks/{owner}/{name}", dependencies=[require_admin])
async def delete_stack(owner: str, name: str, request: Request) -> dict[str, Any]:
    """Stop watching a stack. Its runs, signatures and cases stay: they are
    the record of what it found."""
    repo = f"{owner}/{name}"
    if not await _repo(request).delete(repo):
        raise HTTPException(status_code=404, detail=f"no watched stack {repo}")
    config = request.app.state.config
    await emit_audit(
        getattr(request.app.state, "db", None),
        "watcher_stack_deleted",
        actor=request_actor(request),
        details={"repo": repo},
        audit_log_path=getattr(config, "audit_log_path", None),
    )
    return {"repo": repo, "deleted": True}
