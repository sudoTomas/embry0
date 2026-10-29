"""A repository's areas (spec 2026-10-25): what its ``.embry0/areas.yml`` names, and mapping them with an agent.

``GET /repos/{repo}/areas`` reads the file from the default branch with the
organization's credential and says what the newest map-areas job did.
``POST /repos/{repo}/areas/map`` starts that job, with the model and key a
person chose (run with); it opens a pull request adding or updating the file.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from embry0.api.deps import request_actor
from embry0.api.repo_allowlist import enforce_repo_allowlist
from embry0.api.schemas import RunWithRequest
from embry0.api.v1.jobs import CREATE_A_JOB
from embry0.api.v1.run_with import checked_run_with
from embry0.services.area_map import map_task, start_map_areas_job
from embry0.services.areas import AREAS, AREAS_PATH, read_contents
from embry0.workflows.issue_to_pr.areas_delivery import TEMPLATE

router = APIRouter()


class MapBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The model and key the job runs with; none is the usual run.
    run_with: RunWithRequest | None = None


async def _registered(request: Request, repo: str) -> str:
    registry = getattr(request.app.state, "repos_repo", None)
    registered = await registry.registered_as(repo) if registry is not None else None
    if registered is None:
        raise HTTPException(status_code=404, detail=f"{repo} is not a registered repository")
    return str(registered)


async def _read_file(request: Request, repo: str) -> str | None:
    """The file on the default branch, with the repository's organization credential; None when it cannot be read."""
    from embry0.api.v1.organizations import org_forge_client

    organizations = getattr(request.app.state, "organizations_repo", None)
    org = await organizations.get(repo.split("/", 1)[0]) if organizations is not None else None
    if org is None:
        return None
    client = await org_forge_client(request, org)
    return await read_contents(client.get, repo, AREAS_PATH)


async def _newest_job(request: Request, repo: str) -> dict[str, Any] | None:
    jobs = getattr(request.app.state, "jobs_repo", None)
    newest = (await jobs.newest_per_repo([repo], pipeline_template=TEMPLATE)).get(repo) if jobs is not None else None
    if not newest:
        return None
    return {k: newest.get(k) for k in ("job_id", "status", "created_at", "finished_at", "pr_url", "live")}


@router.get("/repos/{full_repo:repo}/areas")
async def get_areas(full_repo: str, request: Request, fresh: bool = Query(default=False)) -> dict[str, Any]:
    """The areas the file names (read again with ``fresh``), and the newest map-areas job."""
    repo = await _registered(request, full_repo)
    await enforce_repo_allowlist(request, repo, action="read a repository's areas")

    async def read(name: str, path: str) -> str | None:
        return await _read_file(request, name)

    areas = await AREAS.areas(repo, read, fresh=fresh)
    return {
        "repo": repo,
        "path": AREAS_PATH,
        "areas": [
            {"name": a.name, "slug": a.slug, "paths": list(a.paths), "logs": list(a.logs), "owners": list(a.owners)}
            for a in areas
        ],
        "latest_job": await _newest_job(request, repo),
    }


@router.post("/repos/{full_repo:repo}/areas/map", status_code=202)
async def map_areas(full_repo: str, request: Request, body: MapBody | None = None) -> dict[str, Any]:
    """Start an agent job that writes the file and opens its pull request.

    Refused with 409 while an earlier map of the repository is live, 429 while
    the budget gate refuses, and 422 for a model or key that cannot run.
    """
    repo = await _registered(request, full_repo)
    await enforce_repo_allowlist(request, repo, action=CREATE_A_JOB)
    run_with = await checked_run_with(request, body.run_with if body else None, repo)
    newest = await _newest_job(request, repo)
    if newest and newest.get("live"):
        raise HTTPException(status_code=409, detail=f"a map of {repo}'s areas is already running ({newest['job_id']})")
    state = request.app.state
    executor = getattr(state, "issue_executor", None)
    actor = request_actor(request)
    refusal = await executor.dispatch_refusal(actor=actor) if executor is not None else None
    if refusal is not None:
        raise HTTPException(status_code=429, detail=refusal.public_message)
    stacks = getattr(state, "watcher_stacks_repo", None)
    stack = await stacks.get(repo) if stacks is not None else None
    from embry0.services.context_audit import job_budget_usd

    try:
        existing = await _read_file(request, repo)
    except Exception:  # noqa: BLE001 - the agent reads the file in its clone too
        existing = None
    task = map_task(repo, existing=existing, logql=getattr(stack, "logql", None) if stack is not None else None)
    try:
        job_id = await start_map_areas_job(
            repo,
            jobs=state.jobs_repo,
            executor=executor,
            templates=getattr(state, "templates_repo", None),
            task=task,
            budget_usd=job_budget_usd(state.config),
            dispatched_by=actor,
            run_with=run_with,
            db=getattr(state, "db", None),
            audit_log_path=getattr(state.config, "audit_log_path", None),
        )
    except (LookupError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job_id": job_id, "repo": repo}
