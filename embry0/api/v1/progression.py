"""Progression API (spec 2026-10-24): the account, its characters' skills and levels, and its achievements.

Both routes answer 404 while the settings document turns the game layer off
(``ui.game``, ``embry0/api/v1/ui.py``).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request

from embry0.api.deps import require_admin
from embry0.api.repo_allowlist import covered_slugs, deployment_writes, key_covers
from embry0.api.v1.ui import require_game
from embry0.api.v1.world import covered_entries
from embry0.services.achievements import DERIVED_METRICS

# The game's reads: 404 while the settings document turns the game layer off (ui.game, #177).
# The content pack's routes (progression_pack.py) are its configuration and stay.
router = APIRouter(dependencies=[Depends(require_game)])


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
    result = await covered_progression(request, progression, progression.to_dict())
    packs = getattr(request.app.state, "progression_packs", None)
    if packs is not None:
        # The stored version it comes from (0: nothing stored) and whether it is the shipped default's.
        effective = await packs.effective()
        result["pack"] = {**result["pack"], "revision": effective.version, "follows_default": effective.follows_default}
    return result


async def covered_progression(request: Request, progression: Any, result: dict[str, Any]) -> dict[str, Any]:
    """``result``, ``progression.to_dict()``, as the caller's key may read it.

    A repository's diary and the tiers it completed among the latest unlocks
    only to a key that covers it. An achievement's evidence, the step that
    last moved it, only when it names a repository the key covers, or when it
    names characters rather than work (a level, a cape, a rare drop). A
    quest's ``where``, a repository or an issue, only when the key covers it;
    the quest itself shows, as the totals do. Everything else is the
    account's, the same for every caller.
    """
    result["diaries"] = covered_entries(request, result.get("diaries", []))
    result["recent"] = covered_entries(request, result.get("recent", []))
    slugs = covered_slugs(request)
    if slugs is None:
        return result
    for state, shown in zip(progression.achievements, result.get("achievements", []), strict=True):
        if shown.get("evidence") is not None and not _evidence_covered(state, slugs):
            shown["evidence"] = None
    for quest in result.get("quests", []):
        if quest.get("where") is not None and not await _where_covered(request, str(quest["where"])):
            quest["where"] = None
    return result


def _evidence_covered(state: Any, slugs: list[str]) -> bool:
    """Whether an achievement's evidence names characters only, or a repository among ``slugs``, by its pull
    request's or ticket's address or its label (``acme/web #7``)."""
    if state.achievement.entry.metric in DERIVED_METRICS:
        return True
    evidence = state.evidence
    label = str(evidence.label or "").lower()
    path = urlsplit(str(evidence.url or "")).path.lower().rstrip("/")
    return any(
        label == slug or label.startswith(f"{slug} ") or path == f"/{slug}" or path.startswith(f"/{slug}/")
        for slug in slugs
    )


async def _where_covered(request: Request, where: str) -> bool:
    """Whether the caller's key covers a quest's ``where``: a repository, or an issue, by its repository."""
    if "/" in where:
        return key_covers(request, where)
    issues = getattr(request.app.state, "issues_repo", None)
    issue = await issues.get(where) if issues is not None else None
    return issue is not None and key_covers(request, issue.get("repo"))


@router.post(
    "/progression/rebuild", dependencies=[require_admin, deployment_writes("rebuild the deployment's progression")]
)
async def rebuild_progression(request: Request) -> dict[str, Any]:
    """Fold every closed day into the rollup again now, and answer once it is written (admin).

    For work recorded late for a day the rollup already holds, such as after
    a merge backfill; the nightly rebuild adds it within a day otherwise.
    Reads go on meanwhile, from the rollup as it stands.
    """
    result: dict[str, Any] = await _service(request).rebuild()
    return result
