"""Sandbox listing endpoint — ops visibility into running + stale containers."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.get("/sandboxes")
async def list_sandboxes(request: Request, include_stopped: bool = False) -> dict[str, object]:
    """List sandbox-* containers and their ages.

    Shells out to ``docker ps`` (via the app's DockerClient) filtered on the
    ``sandbox-`` name prefix. Returns a list of ``{name, status, running_for}``
    dicts. Ops can cross-reference this with ``ContainerReaper.max_age_hours``
    to spot stale sandboxes before the reaper culls them.

    Returns an empty list if the docker client is unavailable or the command
    fails — this endpoint is strictly observability and must not 5xx the
    dashboard just because the docker socket is momentarily unreachable.
    """
    docker = getattr(request.app.state, "docker", None)
    if docker is None:
        return {"containers": [], "count": 0}

    try:
        base = docker._build_base_cmd()
        ps_args = ["ps"]
        if include_stopped:
            ps_args.append("-a")
        ps_args.extend(
            [
                "--filter",
                "name=sandbox-",
                "--format",
                "{{.Names}}|{{.Status}}|{{.RunningFor}}",
            ]
        )
        output = await docker.run_cmd(base + ps_args, timeout=10)
    except Exception:
        logger.warning("sandbox_list_failed", exc_info=True)
        return {"containers": [], "count": 0}

    containers: list[dict[str, str]] = []
    for line in (output or "").strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        containers.append(
            {
                "name": parts[0],
                "status": parts[1],
                "running_for": parts[2],
            }
        )
    return {"containers": containers, "count": len(containers)}
