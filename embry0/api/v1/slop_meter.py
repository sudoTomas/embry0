"""The Slop-O-Meter's readings of a repository (spec 2026-10-26, slop-o-meter).

``GET /repos/{repo}/slop-meter``: the repository's score and level, each area's,
where reading its history stands, and the week-by-week series of attention
cost, capacity and slop for the repository or one area. After Slop-O-Meter's
published slop score algorithm (https://github.com/slop-o-meter/slop-o-meter).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from embry0.api.repo_allowlist import enforce_repo_allowlist
from embry0.services.slop import meter_view

router = APIRouter()


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date | datetime) else value


@router.get("/repos/{full_repo:path}/slop-meter")
async def read_slop_meter(full_repo: str, request: Request, area: str = Query(default="")) -> dict[str, Any]:
    """The repository's Slop-O-Meter: its score, its areas', and the weekly series of ``area`` ('' the repository)."""
    registry = getattr(request.app.state, "repos_repo", None)
    repo = await registry.registered_as(full_repo) if registry is not None else None
    if repo is None:
        raise HTTPException(status_code=404, detail=f"{full_repo} is not a registered repository")
    repo = str(repo)
    await enforce_repo_allowlist(request, repo, action="read a repository's slop-o-meter")
    store = getattr(request.app.state, "slop_meter_repo", None)
    if store is None:
        raise HTTPException(status_code=503, detail="the slop-o-meter store is not initialized")
    rows = {str(r["area"]): r for r in await store.scores(repo)}
    if area and area not in rows:
        raise HTTPException(status_code=404, detail=f"{repo} has no slop-o-meter reading for the area {area!r}")
    state = await store.state(repo)
    weeks = await store.weeks(repo, area)
    return {
        "repo": repo,
        "meter": meter_view(rows.get("")),
        "areas": [{"area": name, "meter": meter_view(row)} for name, row in sorted(rows.items()) if name],
        "area": area or None,
        "weeks": [
            {
                "week": _iso(w["week"]),
                "cost": round(float(w["cost"]), 2),
                "capacity": round(float(w["capacity"]), 2),
                "slop": round(float(w["slop"]), 2),
                "lines": round(float(w["lines"]), 2),
                "score": round(float(w["score"]), 4),
            }
            for w in weeks
        ],
        "reading": {
            "commits_read": int(state.get("commits_read") or 0),
            "commits_total": int(state.get("commits_total") or 0),
            "computed_at": _iso(state.get("computed_at")),
            "error": state.get("error"),
        },
    }
