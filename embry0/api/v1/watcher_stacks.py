"""Watcher stacks API (#378): what embry0 watches, editable without a redeploy.

Reads are operator-tier like the rest of the watcher API; writes are
admin-tier, because adding a stack points embry0's log analysis — and the
spend behind it — at another deployment.

What a stack reads and where it files are the deployment's, not its
repository's: its LogQL selectors (``logql``, ``settings.deploy_logql``) choose
lines from every container the deployment ships to Loki, its
``context_proxy_container_allowlist`` widens the containers its analysis job
may read, and its tracker target names a team's tracker. A key with a
repository allowlist sets none of them: it changes only the rest of a stack an
admin made for one of its repositories (enabled, thresholds, budget), and
creating a stack is refused to it as the deployment's
(:data:`READS_BEYOND_THE_REPOSITORY`).
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from embry0.api.deps import request_actor, require_admin
from embry0.api.repo_allowlist import (
    covered_slugs,
    enforce_deployment_allowlist,
    enforce_repo_allowlist,
    key_covers,
    restricted_key_name,
)
from embry0.api.schemas import ServableFields
from embry0.audit.helpers import emit_audit
from embry0.services.watcher_stacks import SETTING_KEYS, WatcherStack, check_settings
from embry0.storage.repositories.watcher_stacks import WatcherStacksRepository

router = APIRouter()


class StackBody(ServableFields):
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


#: The settings of a stack that reach beyond its repository: a selector over every container's logs, and the containers
#: its analysis job may read through the context proxy.
READS_BEYOND_THE_REPOSITORY: tuple[str, ...] = ("deploy_logql", "context_proxy_container_allowlist")


def _beyond(stack: WatcherStack | None) -> tuple[Any, ...] | None:
    """What of ``stack`` is the deployment's to choose: its selectors, the containers it reads and where it files."""
    if stack is None:
        return None
    return (
        stack.logql,
        stack.tracker,
        dict(stack.tracker_target),
        *(stack.settings.get(key) for key in READS_BEYOND_THE_REPOSITORY),
    )


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
    """Every watched stack; to a key with an allowlist, those of the repositories it covers."""
    found = await _repo(request).list_all(enabled_only=enabled_only, repos=covered_slugs(request))
    stacks = [stack for stack in found if key_covers(request, stack.repo)]
    return {"stacks": [s.to_dict() for s in stacks], "overridable_settings": sorted(SETTING_KEYS)}


@router.get("/watcher/stacks/{owner}/{name}")
async def get_stack(owner: str, name: str, request: Request) -> dict[str, Any]:
    # A repository the caller's key does not cover has no stack for it (embry0.api.repository_reads).
    stack = await _repo(request).get(f"{owner}/{name}") if key_covers(request, f"{owner}/{name}") else None
    if stack is None:
        raise HTTPException(status_code=404, detail=f"no watched stack {owner}/{name}")
    return stack.to_dict()


@router.put("/watcher/stacks/{owner}/{name}", dependencies=[require_admin])
async def put_stack(owner: str, name: str, body: StackBody, request: Request) -> dict[str, Any]:
    """Create or replace a watched stack. Unknown ``settings`` keys are
    dropped rather than stored — the response shows what was kept. A known key
    with a wrong type or a value out of range is a 422 naming it (#172): the
    stored value is what every tick reads."""
    await enforce_repo_allowlist(request, f"{owner}/{name}", action="change a watched stack")
    repo = f"{owner}/{name}"
    unknown = sorted(set(body.settings) - SETTING_KEYS)
    try:
        settings = check_settings(body.settings)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    stack = WatcherStack(
        repo=repo,
        logql=body.logql,
        tracker=body.tracker,
        tracker_target=body.tracker_target,
        settings=settings,
        enabled=body.enabled,
    )
    if restricted_key_name(request) is not None and _beyond(stack) != _beyond(await _repo(request).get(repo)):
        # Its selectors read every container's logs, and its tracker files into a team's: the deployment's.
        await enforce_deployment_allowlist(request, action="choose what a watched stack reads or where it files")
    stored = await save_stack(request, stack, ignored_settings=unknown)
    return {**stored.to_dict(), "ignored_settings": unknown}


@router.delete("/watcher/stacks/{owner}/{name}", dependencies=[require_admin])
async def delete_stack(owner: str, name: str, request: Request) -> dict[str, Any]:
    """Stop watching a stack. Its runs, signatures and cases stay: they are
    the record of what it found."""
    await enforce_repo_allowlist(request, f"{owner}/{name}", action="change a watched stack")
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
