"""Sandbox listing endpoint — ops visibility into running + stale containers."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request

from embry0.api.job_allowlist import artifact_job
from embry0.api.repo_allowlist import covered_slugs, key_covers

logger = structlog.get_logger(__name__)
router = APIRouter()

_PREFIX = "sandbox-"


def _job_of(name: str) -> str:
    """The job a sandbox container runs: ``sandbox-<job_id>``, or ``sandbox-<job_id>__<app>`` for a QA sub-task."""
    return artifact_job(name.removeprefix(_PREFIX))


@router.get("/sandboxes/active")
async def list_active_sandboxes(request: Request, include_stopped: bool = False) -> dict[str, object]:
    """List sandbox-* containers and their ages.

    Lists containers through the app's runtime (``docker ps``), filtered on the
    ``sandbox-`` name prefix. Returns a list of ``{name, status, running_for}``
    dicts. Ops can cross-reference this with ``ContainerReaper.max_age_hours``
    to spot stale sandboxes before the reaper culls them.

    Returns an empty list if the runtime is unavailable or the command
    fails — this endpoint is strictly observability and must not 5xx the
    dashboard just because the docker socket is momentarily unreachable.

    A key with a repository allowlist is shown only the sandboxes of jobs in
    the repositories it covers (``embry0.api.job_allowlist``); a container
    whose name names no such job is left out.
    """
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        return {"containers": [], "count": 0}

    try:
        found = await runtime.list_containers(name=_PREFIX, include_stopped=include_stopped, timeout=10)
    except Exception:
        logger.warning("sandbox_list_failed", exc_info=True)
        return {"containers": [], "count": 0}
    if covered_slugs(request) is not None:
        repos = await request.app.state.jobs_repo.repositories_of({_job_of(c.name) for c in found})
        found = [c for c in found if key_covers(request, repos.get(_job_of(c.name)))]

    containers = [{"name": c.name, "status": c.status, "running_for": c.running_for} for c in found]
    return {"containers": containers, "count": len(containers)}
