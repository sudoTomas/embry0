"""The settings document's ``budget`` section as an API route reads it: one snapshot per request.

A route that starts a job reads the cap it gives the job here, at the moment
it starts it, so a change to the section holds from the next request with no
restart. A repository's caps are its organization's layer over the
deployment's (:meth:`embry0.settings.store.Snapshot.get`).
"""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException, Request

from embry0.settings.sections.budget import BUDGET, BudgetSettings

#: The caps of the jobs embry0 starts with nobody watching them, each a hard stop.
UnattendedCap = Literal["context_audit_job_usd", "area_map_job_usd", "fix_job_usd"]


async def budget_settings(request: Request, *, repo: str | None = None) -> BudgetSettings:
    """The budget now: the deployment's, or as ``repo`` reads it, through the app's budget policy (the old
    readers for a key not imported yet); a bare settings store's snapshot without one. 503 without either."""
    policy = getattr(request.app.state, "budget_policy", None)
    if policy is not None:
        view = await policy.view()
        settings: BudgetSettings = view.repository(repo) if repo else view.deployment()
        return settings
    store = getattr(request.app.state, "settings", None)
    if store is None:
        raise HTTPException(status_code=503, detail="settings storage not initialized")
    settings = (await store.snapshot()).get(BUDGET, repo=repo)
    return settings


async def unattended_cap(request: Request, repo: str, key: UnattendedCap) -> float:
    """The hard cap in USD a job of ``repo`` gets, read now from the section key ``key``."""
    value: float = getattr(await budget_settings(request, repo=repo), key)
    return value
