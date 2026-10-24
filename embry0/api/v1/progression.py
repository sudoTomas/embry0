"""Progression API (spec 2026-10-24): the account, its characters' skills and levels, and its achievements."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/progression")
async def read_progression(request: Request) -> dict[str, Any]:
    """The account's experience and totals, each character's 23 skills, the achievements and the latest unlocks.

    Recomputed from the jobs, traces, healing cases and watcher runs at most
    once a minute; read-only.
    """
    service = getattr(request.app.state, "progression", None)
    if service is None:
        raise HTTPException(status_code=503, detail="progression is not initialized")
    progression = await service.read()
    result: dict[str, Any] = progression.to_dict()
    return result
