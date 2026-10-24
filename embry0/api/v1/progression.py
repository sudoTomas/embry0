"""Progression API (spec 2026-10-24): the instance's skills, total level and each agent's level."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/progression")
async def read_progression(request: Request) -> dict[str, Any]:
    """Each skill's experience and level, the total level, each agent's level and tier, and the latest level-ups.

    Recomputed from the jobs, traces, healing cases and watcher runs at most
    once a minute; read-only.
    """
    service = getattr(request.app.state, "progression", None)
    if service is None:
        raise HTTPException(status_code=503, detail="progression is not initialized")
    progression = await service.read()
    result: dict[str, Any] = progression.to_dict()
    return result
