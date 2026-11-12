"""Watcher API (RAV-657) — run audit trail + manual trigger."""

import asyncio
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from embry0.api.repo_allowlist import covered_slugs, enforce_repo_allowlist, hide_unless_covered, key_covers
from embry0.api.schemas import RunWithRequest
from embry0.api.v1.run_with import checked_run_with

router = APIRouter()


@router.get("/watcher/runs")
async def list_watcher_runs(request: Request, limit: int = 50, stack: str | None = None) -> list[dict[str, Any]]:
    """Recent ticks, newest first. ``stack`` ("owner/name") narrows to one
    watched stack; omitted, every stack's ticks are interleaved. A key with a
    repository allowlist reads only its repositories' ticks, and the jobs
    they started, narrowed before the limit."""
    runs_repo = getattr(request.app.state, "watcher_runs_repo", None)
    if runs_repo is None:
        raise HTTPException(status_code=503, detail="watcher storage not initialized")
    ticks = await runs_repo.list_recent(limit=min(max(limit, 1), 200), repo=stack, repos=covered_slugs(request))
    return cast("list[dict[str, Any]]", ticks)


class WatcherRunBody(BaseModel):
    """Optional: the model and key the tick's analysis runs with (spec 2026-10-25)."""

    model_config = ConfigDict(extra="forbid")

    run_with: RunWithRequest | None = None


@router.post("/watcher/run")
async def trigger_watcher_run(
    request: Request, stack: str | None = None, body: WatcherRunBody | None = None
) -> dict[str, Any]:
    """Run one watcher tick now (operator/acceptance use).

    ``stack`` ("owner/name") ticks that stack. Omitted while several stacks
    are watched, every stack ticks, concurrently, and the answer is
    ``{"action": "every_stack_ticked", "stacks": [{"stack": ..., <that
    tick's answer>}, ...]}`` in stack order. Each tick may run one paid
    analysis under its own stack's caps.

    A key with a repository allowlist is answered as a deployment watching
    only the stacks it covers (embry0.api.repository_reads): omitted, the
    stacks it covers tick; a stack it does not cover answers as one nobody
    watches, whether it is watched or not, and the refusal is logged and
    audited; covering none, it is answered as a deployment watching none.

    Works even when the scheduled loop is disabled (`WATCHER_ENABLED=false`)
    so the flow can be exercised deliberately; the tick still enforces every
    guardrail (proposal cap, dedup, min-signal, human-gate-by-label).
    ``run_with`` must be one every ticking stack may use.
    """
    chosen = body.run_with if body else None
    services = getattr(request.app.state, "watcher_services", None) or {}
    if not services:
        single = getattr(request.app.state, "watcher_service", None)
        if single is None:
            raise HTTPException(status_code=503, detail="watcher not initialized")
        # A single unnamed watcher has no stack to check a restricted key against.
        await enforce_repo_allowlist(request, stack, action="run the watcher")
        run_with = await checked_run_with(request, chosen, stack)
        return cast("dict[str, Any]", await single.run_once(run_with))
    # The stacks the caller's key covers: every one for a caller without an allowlist.
    watched = {name: service for name, service in services.items() if key_covers(request, name)}
    if not watched or (stack and stack not in watched):
        # A stack the key does not cover answers as one nobody watches, and a key covering none of them is
        # answered as a deployment watching none (above); a stack it does not cover is a refusal on record.
        unwatched = (
            HTTPException(status_code=404, detail=f"{stack} is not a watched stack")
            if watched
            else HTTPException(status_code=503, detail="watcher not initialized")
        )
        if stack:
            await hide_unless_covered(request, stack, unwatched, action="run the watcher")
        raise unwatched
    if stack:
        run_with = await checked_run_with(request, chosen, stack)
        return cast("dict[str, Any]", await watched[stack].run_once(run_with))
    if len(watched) > 1:
        # The dashboard's Run tick now and MCP watcher_trigger name no stack;
        # unnamed means all of them, never one picked for the caller.
        names = sorted(watched)
        chosen_for: dict[str, Any] = {}
        for name in names:
            chosen_for[name] = await checked_run_with(request, chosen, name)
        results = await asyncio.gather(*(watched[name].run_once(chosen_for[name]) for name in names))
        return {
            "action": "every_stack_ticked",
            "stacks": [{"stack": name, **result} for name, result in zip(names, results, strict=True)],
        }
    only_stack, service = next(iter(watched.items()))
    run_with = await checked_run_with(request, chosen, only_stack)
    return cast("dict[str, Any]", await service.run_once(run_with))
