"""Watcher API (RAV-657) — run audit trail + manual trigger."""

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/watcher/runs")
async def list_watcher_runs(request: Request, limit: int = 50) -> list[dict[str, Any]]:
    repo = getattr(request.app.state, "watcher_runs_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="watcher storage not initialized")
    return cast("list[dict[str, Any]]", await repo.list_recent(limit=min(max(limit, 1), 200)))


@router.post("/watcher/run")
async def trigger_watcher_run(request: Request) -> dict[str, Any]:
    """Run one watcher tick now (operator/acceptance use).

    Works even when the scheduled loop is disabled (`WATCHER_ENABLED=false`)
    so the flow can be exercised deliberately; the tick still enforces every
    guardrail (proposal cap, dedup, min-signal, human-gate-by-label).
    """
    watcher = getattr(request.app.state, "watcher_service", None)
    if watcher is None:
        raise HTTPException(status_code=503, detail="watcher not initialized")
    return cast("dict[str, Any]", await watcher.run_once())
