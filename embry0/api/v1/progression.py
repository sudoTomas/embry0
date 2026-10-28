"""Progression API (spec 2026-10-24): the account, its characters' skills and levels, and its achievements."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/progression")
async def read_progression(request: Request) -> dict[str, Any]:
    """The account's experience and totals, each character's skills, the achievements and the latest unlocks.

    Recomputed under the active content pack (#176) from the jobs, traces,
    healing cases and answers at most once a minute; read-only. The answer
    carries the pack's names, colours, words and art too, so the dashboard
    keeps no copy of them.
    """
    service = getattr(request.app.state, "progression", None)
    if service is None:
        raise HTTPException(status_code=503, detail="progression is not initialized")
    progression = await service.read()
    result: dict[str, Any] = progression.to_dict()
    return result
