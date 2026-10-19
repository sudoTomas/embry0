"""Watcher API (RAV-657) — run audit trail + manual trigger."""

import asyncio
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request

from embry0.api.repo_allowlist import enforce_repo_allowlist

router = APIRouter()


@router.get("/watcher/runs")
async def list_watcher_runs(request: Request, limit: int = 50, stack: str | None = None) -> list[dict[str, Any]]:
    """Recent ticks, newest first. ``stack`` ("owner/name") narrows to one
    watched stack; omitted, every stack's ticks are interleaved."""
    runs_repo = getattr(request.app.state, "watcher_runs_repo", None)
    if runs_repo is None:
        raise HTTPException(status_code=503, detail="watcher storage not initialized")
    return cast("list[dict[str, Any]]", await runs_repo.list_recent(limit=min(max(limit, 1), 200), repo=stack))


@router.post("/watcher/run")
async def trigger_watcher_run(request: Request, stack: str | None = None) -> dict[str, Any]:
    """Run one watcher tick now (operator/acceptance use).

    ``stack`` ("owner/name") ticks that stack. Omitted while several stacks
    are watched, every stack ticks, concurrently, and the answer is
    ``{"action": "every_stack_ticked", "stacks": [{"stack": ..., <that
    tick's answer>}, ...]}`` in stack order. Each tick may run one paid
    analysis under its own stack's caps. A restricted key must be allowed
    every stack, all checked before any tick starts.

    Works even when the scheduled loop is disabled (`WATCHER_ENABLED=false`)
    so the flow can be exercised deliberately; the tick still enforces every
    guardrail (proposal cap, dedup, min-signal, human-gate-by-label).
    """
    services = getattr(request.app.state, "watcher_services", None) or {}
    if not services:
        single = getattr(request.app.state, "watcher_service", None)
        if single is None:
            raise HTTPException(status_code=503, detail="watcher not initialized")
        # A single unnamed watcher has no stack to check a restricted key against.
        await enforce_repo_allowlist(request, stack, action="run the watcher")
        return cast("dict[str, Any]", await single.run_once())
    if stack:
        if stack not in services:
            raise HTTPException(status_code=404, detail=f"{stack} is not a watched stack")
        await enforce_repo_allowlist(request, stack, action="run the watcher")
        return cast("dict[str, Any]", await services[stack].run_once())
    if len(services) > 1:
        # The dashboard's Run tick now and MCP watcher_trigger name no stack;
        # unnamed means all of them, never one picked for the caller.
        names = sorted(services)
        for name in names:
            await enforce_repo_allowlist(request, name, action="run the watcher")
        results = await asyncio.gather(*(services[name].run_once() for name in names))
        return {
            "action": "every_stack_ticked",
            "stacks": [{"stack": name, **result} for name, result in zip(names, results, strict=True)],
        }
    only_stack, service = next(iter(services.items()))
    await enforce_repo_allowlist(request, only_stack, action="run the watcher")
    return cast("dict[str, Any]", await service.run_once())
