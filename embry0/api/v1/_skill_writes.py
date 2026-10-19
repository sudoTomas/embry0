"""One skill-name check for the v1 routers that write skills (spec 3.3).

The classification lives in ``embry0.agents.skill_catalog``; this turns an
unknown name into the 422 every write answers with.
"""

from __future__ import annotations

from collections.abc import Iterable

from fastapi import HTTPException, Request

from embry0.agents.skill_catalog import validate_skill_write
from embry0.agents.skills import UnknownSkillError


async def check_added_skills(request: Request, names: Iterable[str]) -> list[str]:
    """The warnings for the names a write adds; 422 naming the closest known names for an unknown one."""
    try:
        return await validate_skill_write(getattr(request.app.state, "skill_sources_repo", None), names)
    except UnknownSkillError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
