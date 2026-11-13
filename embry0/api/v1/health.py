"""Health check endpoints.

``GET /health/ready`` is mounted without authentication, so a monitor or the
deploy script reads it without a key. Its overall state answers every
caller: ``status``, and the checks that name nothing, ``database`` and
``watcher`` (the worst state across the watched stacks). ``watcher_stacks``,
each watched stack's own state under its repository, answers only a caller
the authenticated routes would let read
(:func:`~embry0.api.deps.authenticated_reader`), and a key with a repository
allowlist only for the repositories its list covers, as ``GET
/watcher/stacks`` lists them. A header that is not a live key reads as no
key, never as a 401: the answer is then the overall state alone.
"""

from typing import Any

from fastapi import APIRouter, Request

from embry0.api.deps import authenticated_reader
from embry0.api.repo_allowlist import key_covers

router = APIRouter()

_HEALTHY = frozenset({"ok", "unverified"})


@router.get("/health/ready")
async def health_ready(request: Request) -> dict[str, object]:
    checks: dict[str, Any] = {}
    db = getattr(request.app.state, "db", None)
    if db and db.pool:
        try:
            await db.fetchval("SELECT 1")
            checks["database"] = "ok"
        except Exception:
            checks["database"] = "error"
    else:
        checks["database"] = "not_connected"
    # #270: only reported while the scheduled loop runs. "unverified" (Loki
    # has not answered yet) is shown but does not degrade readiness — it is
    # no evidence of a fault, and embry0 must be ready without its log store.
    # Every other non-ok value (missing_logql, invalid_logql,
    # loki_misconfigured) is an operator-fixable fault that has stopped ticks.
    config = getattr(request.app.state, "config", None)
    # #378: one watcher per watched stack. `watcher` stays the single value
    # alerting already reads — the WORST across stacks, so a stuck stack
    # cannot hide behind a busy one — and `watcher_stacks` says which.
    services = getattr(request.app.state, "watcher_services", None)
    if services is None:
        single = getattr(request.app.state, "watcher_service", None)
        services = {getattr(single, "_repo", ""): single} if single is not None else {}
    if services and getattr(config, "watcher_enabled", False):
        per_stack = {repo: service.health for repo, service in services.items()}
        checks["watcher"] = _worst(per_stack.values())
        # Names repositories: a caller with a key or a session only, and of a key's list only what it covers.
        if await authenticated_reader(request):
            checks["watcher_stacks"] = {repo: state for repo, state in per_stack.items() if key_covers(request, repo)}
    flat = [v for v in checks.values() if isinstance(v, str)]
    status = "ok" if all(v in _HEALTHY for v in flat) else "degraded"
    return {"status": status, "checks": checks}


def _worst(states: Any) -> str:
    """The least healthy state, so one stuck stack degrades the endpoint.
    Ordering: anything unhealthy beats "unverified" (no evidence of a fault)
    beats "ok"."""
    seen = list(states) or ["ok"]
    unhealthy = [s for s in seen if s not in _HEALTHY]
    if unhealthy:
        return str(unhealthy[0])
    return "unverified" if any(s != "ok" for s in seen) else "ok"
