"""POST /repos/bootstrap — one-command new-project bootstrap (EMB-49)."""

from __future__ import annotations

import re
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from embry0.api.repo_allowlist import enforce_repo_allowlist
from embry0.api.schemas.repo_path import validate_repo_path
from embry0.services.bootstrap import BootstrapError, bootstrap_repo

router = APIRouter()

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
        return validate_repo_path(v)

    @field_validator("app")
    @classmethod
    def _app_shape(cls, v: str | None) -> str | None:
        if v is not None and not _APP_NAME_PATTERN.fullmatch(v):
            raise ValueError("app must be a lowercase slug (a-z, 0-9, -, _)")
        return v

    @field_validator("frontend_url", "ready_check_url")
    @classmethod
    def _address_scheme(cls, v: str | None) -> str | None:
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("Start the address with http:// or https://")
        return v


def _first_error(exc: yaml.YAMLError | ValidationError) -> str:
    """One readable line for the starter's own parse failure."""
    if isinstance(exc, ValidationError):
        err = exc.errors()[0]
        where = ".".join(str(part) for part in err["loc"])
        return f"{err['msg']} ({where})" if where else err["msg"]
    problem = getattr(exc, "problem", None)
    mark = getattr(exc, "problem_mark", None)
    if problem and mark is not None:
        return f"{problem} (line {mark.line + 1})"
    return str(exc).splitlines()[0]


@router.post("/repos/bootstrap")
async def bootstrap(req: BootstrapRequest, request: Request) -> dict[str, Any]:
    await enforce_repo_allowlist(request, req.repo, action="bootstrap a repository")
    config = request.app.state.config
    try:
        result = await bootstrap_repo(
            req.repo,
            github_token=getattr(config, "github_token", "") or "",
            qa_configs_repo=request.app.state.qa_configs_repo,
            prefs_repo=getattr(request.app.state, "repo_preferences_repo", None),
            profiles_repo=getattr(request.app.state, "profiles_repo", None),
            app=req.app,
            boot_command=req.boot_command,
            frontend_url=req.frontend_url,
            ready_check_url=req.ready_check_url,
            sandbox_profile=req.sandbox_profile,
            force=req.force,
            credential_resolver=getattr(request.app.state, "credential_resolver", None),
        )
    except BootstrapError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (yaml.YAMLError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=f"That setup could not be read: {_first_error(exc)}") from exc
    return result.as_dict()
