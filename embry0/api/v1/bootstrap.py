"""POST /repos/bootstrap — one-command new-project bootstrap (EMB-49)."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, field_validator

from embry0.services.bootstrap import BootstrapError, bootstrap_repo

router = APIRouter()

_REPO_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
_APP_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class BootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    app: str | None = None
    boot_command: str | None = None
    frontend_url: str | None = None
    ready_check_url: str | None = None
    sandbox_profile: str | None = None
    force: bool = False

    @field_validator("repo")
    @classmethod
    def _repo_shape(cls, v: str) -> str:
        if not _REPO_PATTERN.match(v):
            raise ValueError("repo must be in 'owner/name' form")
        return v

    @field_validator("app")
    @classmethod
    def _app_shape(cls, v: str | None) -> str | None:
        if v is not None and not _APP_NAME_PATTERN.match(v):
            raise ValueError("app must be a lowercase slug (a-z, 0-9, -, _)")
        return v


@router.post("/repos/bootstrap")
async def bootstrap(req: BootstrapRequest, request: Request) -> dict[str, Any]:
    config = request.app.state.config
    try:
        result = await bootstrap_repo(
            req.repo,
            github_token=getattr(config, "github_token", "") or "",
            prefs_repo=getattr(request.app.state, "repo_preferences_repo", None),
            profiles_repo=getattr(request.app.state, "profiles_repo", None),
            app=req.app,
            boot_command=req.boot_command,
            frontend_url=req.frontend_url,
            ready_check_url=req.ready_check_url,
            sandbox_profile=req.sandbox_profile,
            force=req.force,
        )
    except BootstrapError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.as_dict()
