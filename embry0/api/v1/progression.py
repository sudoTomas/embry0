"""Progression API (spec 2026-10-24): the account, its characters' skills and levels, and its achievements."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from embry0.api.deps import require_admin

router = APIRouter()


def _service(request: Request) -> Any:
    service = getattr(request.app.state, "progression", None)
    if service is None:
        raise HTTPException(status_code=503, detail="progression is not initialized")
    return service


@router.get("/progression")
async def read_progression(request: Request) -> dict[str, Any]:
    """The account's experience and totals, each character's skills, the achievements and the latest unlocks.

    Recomputed under the active content pack (#176) at most once a minute;
    read-only. The experience of the days that ended more than 48 hours ago
    comes from the rollup (``rollup`` in the answer), the rest from the jobs,
    traces, healing cases and answers. The answer carries the pack's names,
    colours, words and art too, so the dashboard keeps no copy of them.
    """
    progression = await _service(request).read()
    result: dict[str, Any] = progression.to_dict()
    packs = getattr(request.app.state, "progression_packs", None)
    if packs is not None:
        # The stored version it comes from (0: nothing stored) and whether it is the shipped default's.
        effective = await packs.effective()
        result["pack"] = {**result["pack"], "revision": effective.version, "follows_default": effective.follows_default}
    return result


@router.post("/progression/rebuild", dependencies=[require_admin])
async def rebuild_progression(request: Request) -> dict[str, Any]:
    """Fold every closed day into the rollup again now, and answer once it is written (admin).

    For work recorded late for a day the rollup already holds, such as after
    a merge backfill; the nightly rebuild adds it within a day otherwise.
    Reads go on meanwhile, from the rollup as it stands.
    """
    result: dict[str, Any] = await _service(request).rebuild()
    return result
