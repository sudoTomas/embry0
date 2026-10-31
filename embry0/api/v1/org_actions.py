"""Organization actions API (ADR 0010): act on the needs list.

``POST /orgs/{owner}/actions`` runs one action (register, hook, onboard,
watch, deactivate) over a list of repositories under the owner and reports
one outcome per repository. Admin-tier like every other organization write:
a hook or a registration points embry0's credentials, and later its spend,
at a repository. Every action is idempotent and ``dry_run`` answers what
would happen without touching anything.

Onboard and watch go through the same functions as ``POST /jobs`` and
``PUT /watcher/stacks/{owner}/{name}``, so they run the same checks (the
caller's repository allowlist among them) and write the same audit rows.
A run that changed anything starts a rediscovery the way
``POST /orgs/{owner}/discover`` does, so the needs list catches up.
"""

from __future__ import annotations

from functools import partial
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from embry0.api.deps import request_actor, require_admin
from embry0.api.repo_allowlist import enforce_owner_allowlist, enforce_repo_allowlist, refusal_detail
from embry0.api.schemas import RunWithRequest
from embry0.api.v1 import jobs as jobs_api
from embry0.api.v1.organizations import _facts_repo, _repo, _token_for, start_discovery
from embry0.api.v1.run_with import checked_run_with
from embry0.api.v1.watcher_stacks import save_stack
from embry0.audit.helpers import emit_audit
from embry0.services import org_actions
from embry0.services.org_actions import ActionContext
from embry0.services.organizations import Organization
from embry0.services.watcher_stacks import WatcherStack

logger = structlog.get_logger(__name__)
router = APIRouter()


class ActionBody(BaseModel):
    action: str = Field(pattern="^(register|hook|onboard|watch|deactivate)$")
    repos: list[str] = Field(min_length=1, max_length=500, description="full repository paths under the owner")
    dry_run: bool = Field(default=False, description="report what would happen; change nothing")
    force: bool = Field(
        default=False,
        description="onboard only: start another onboard job even when the last one completed without a QA config",
    )
    run_with: RunWithRequest | None = Field(
        default=None, description="onboard only: the model and key the onboard jobs run with"
    )


class _JobStarter:
    """Onboard jobs through ``POST /jobs``'s own function, as the calling principal."""

    def __init__(self, request: Request, run_with: RunWithRequest | None = None) -> None:
        self._request = request
        self._run_with = run_with

    def refusal(self, repo: str) -> str | None:
        return jobs_api.onboard_refusal(self._request, repo)

    async def start_onboard_job(self, repo: str, branch: str) -> str:
        try:
            # Checked per repository: the key must be one each of them may use.
            run_with = await checked_run_with(self._request, self._run_with, repo)
            return await jobs_api.start_onboard_job(self._request, repo=repo, branch=branch, run_with=run_with)
        except HTTPException as exc:
            raise org_actions.Refused(str(exc.detail)) from None


class _Stacks:
    """The watcher stacks table, written through ``PUT /watcher/stacks``'s own function."""

    def __init__(self, request: Request, repository: Any) -> None:
        self._request = request
        self._repository = repository

    async def list_all(self) -> list[WatcherStack]:
        return list(await self._repository.list_all())

    async def upsert(self, stack: WatcherStack) -> WatcherStack:
        try:
            return await save_stack(self._request, stack)
        except HTTPException as exc:
            raise org_actions.Refused(str(exc.detail)) from None


async def _forge_client(request: Request, owner: str, forge: str, credential_id: str | None) -> Any:
    """A GitHub client acting with the owner's credential, as probe and discovery do."""
    token, _source = await _token_for(request, owner, forge, credential_id)
    from embry0.execution.github_client import GitHubClient

    return GitHubClient(default_token=token, max_attempts=2)


async def _refusal(request: Request, action: str, repo: str, *, dry_run: bool) -> str | None:
    """What the caller's key is refused with on ``repo``, or None. A real run logs and audits the refusal,
    as every route that refuses a repository does; a dry run only says what it would be."""
    if dry_run:
        return refusal_detail(request, repo, action=action)
    try:
        await enforce_repo_allowlist(request, repo, action=action)
    except HTTPException as exc:
        return str(exc.detail)
    return None


async def _rediscover(request: Request, org: Organization) -> str | None:
    """Start a discovery run for ``org``; None when one is already running or it cannot start."""
    if await _facts_repo(request).running_run(org.owner) is not None:
        return None
    try:
        run = await start_discovery(request, org)
    except HTTPException as exc:
        # The actions already ran; a credential problem here must not hide their results.
        logger.warning("org_action_rediscovery_not_started", owner=org.owner, detail=str(exc.detail))
        return None
    return str(run["id"])


@router.post("/orgs/{owner}/actions", dependencies=[require_admin])
async def run_organization_action(owner: str, body: ActionBody, request: Request) -> dict[str, Any]:
    """Run one action over repositories the owner's discovery saw.

    Outcomes per repository: ``done`` (changed, or would change under
    ``dry_run``), ``already`` (the state exists), ``skipped`` (a reason the
    caller can fix, in ``detail``), ``failed``. A second identical call
    reports ``already`` for every repository the first one changed, and
    changes nothing. ``rediscovery`` is the discovery run a real call that
    changed something started, or null.
    """
    await enforce_owner_allowlist(request, owner, action=f"run the {body.action} action")
    org = await _repo(request).get(owner)
    if org is None:
        raise HTTPException(status_code=404, detail=f"no organization {owner}")
    if org.status != "active":
        raise HTTPException(status_code=409, detail=f"organization {org.owner} is {org.status}")
    facts = {row["repo"].lower(): row for row in await _facts_repo(request).list_for_owner(org.owner)}
    st = request.app.state
    config = getattr(st, "config", None)
    access = org.access if isinstance(org.access, dict) else {}
    hooks_allowed = access.get("hooks") if isinstance(access.get("hooks"), bool) else None
    webhook_url = org_actions.webhook_url_for(config)
    webhook_secret = str(getattr(config, "github_webhook_secret", "") or "")
    forge_client = None
    # The owner's token is fetched only when a hook could actually be made:
    # an action that is skipped for every repository needs no credential.
    if body.action == "hook" and org.forge == "github" and hooks_allowed and webhook_url and webhook_secret:
        forge_client = await _forge_client(request, org.owner, org.forge, org.credential_id)
    stacks_repo = getattr(st, "watcher_stacks_repo", None)
    ctx = ActionContext(
        owner=org.owner,
        forge=org.forge,
        facts=facts,
        registry=getattr(st, "repos_repo", None),
        qa_configs=getattr(st, "qa_configs_repo", None),
        stacks=_Stacks(request, stacks_repo) if stacks_repo is not None else None,
        jobs=getattr(st, "jobs_repo", None),
        job_starter=_JobStarter(request, body.run_with),
        forge_client=forge_client,
        hooks_allowed=hooks_allowed,
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
        force=body.force,
        refusal=partial(_refusal, request, f"run the {body.action} action", dry_run=body.dry_run),
    )
    results = await org_actions.run_action(ctx, body.action, body.repos, dry_run=body.dry_run)
    rediscovery = None
    if not body.dry_run:
        await emit_audit(
            getattr(st, "db", None),
            "organization.action",
            actor=request_actor(request),
            details={
                "owner": org.owner,
                "action": body.action,
                "force": body.force,
                "results": [{"repo": r.repo, "outcome": r.outcome} for r in results],
            },
            audit_log_path=getattr(config, "audit_log_path", None),
        )
        if any(r.outcome == "done" for r in results):
            rediscovery = await _rediscover(request, org)
    return {
        "owner": org.owner,
        "action": body.action,
        "dry_run": body.dry_run,
        "results": [r.to_dict() for r in results],
        "rediscovery": rediscovery,
    }
