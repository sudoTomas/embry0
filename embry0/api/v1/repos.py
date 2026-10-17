"""Repo registry endpoints (RAV-1251).

The registry is the authoritative list of target repos embry0 knows. A repo
appears here when registered explicitly, when its QA config is written, or
via the migration-52 backfill from pre-registry configured surfaces. The
detail endpoint aggregates the repo-scoped state that already lives in
sibling tables so the UI can render one unified repo view.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, field_validator

from embry0.api.deps import request_actor, require_admin
from embry0.api.schemas.repo_path import validate_repo_path
from embry0.audit.helpers import emit_audit
from embry0.storage.repositories.repos import SUPPORTED_FORGES

router = APIRouter()


class RepoRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    notes: str = ""
    # Spec 1A §3.8: deliberately NOT defaulted to the module constant. A
    # deployment that sets ``DEFAULT_FORGE=""`` does so to stop embry0
    # guessing github, and a request that omits the forge must inherit THAT
    # decision — a guessed ``github`` row is worse than no row, because from
    # then on ``forge_for`` succeeds, ERR_REPO_UNREGISTERED never fires, and
    # every sandbox clones the wrong host. None means "unspecified"; the
    # endpoint resolves it against the running config.
    forge: str | None = None

    @field_validator("forge")
    @classmethod
    def _known_forge(cls, v: str | None) -> str | None:
        if v is not None and v not in SUPPORTED_FORGES:
            raise ValueError(f"forge must be one of {list(SUPPORTED_FORGES)}")
        return v

    @field_validator("repo")
    @classmethod
    def _repo_shape(cls, v: str) -> str:
        return validate_repo_path(v)


class RepoActiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool


class RepoDockerAccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


@router.get("/repos")
async def list_repos(request: Request, include_inactive: bool = False) -> list[dict[str, Any]]:
    repos_repo = request.app.state.repos_repo
    configs_repo = request.app.state.qa_configs_repo
    rows = await repos_repo.list_all(include_inactive=include_inactive)
    configured = set(await configs_repo.list_repos())
    # RAV-1294: annotate each repo with its env-var footprint so pickers can
    # show where variables live without N per-repo requests.
    env_counts = await request.app.state.env_repo.counts_by_repo()
    return [
        {
            **r,
            "has_qa_config": r["repo"] in configured,
            "env_var_count": env_counts.get(r["repo"], {}).get("total", 0),
            "env_secret_count": env_counts.get(r["repo"], {}).get("secrets", 0),
        }
        for r in rows
    ]


@router.post("/repos", status_code=201)
async def register_repo(req: RepoRegisterRequest, request: Request) -> dict[str, Any]:
    """Register a repo. An omitted ``forge`` inherits the deployment default
    (``DEFAULT_FORGE``); when that is empty the request is refused rather
    than guessed (Spec 1A §3.8)."""
    repos_repo = request.app.state.repos_repo
    forge = req.forge or getattr(request.app.state.config, "default_forge", "") or ""
    if not forge:
        raise HTTPException(
            status_code=422,
            detail=(
                "forge is required: this deployment runs with DEFAULT_FORGE empty, "
                f'so it never guesses one. Re-send with "forge" set to one of {list(SUPPORTED_FORGES)}.'
            ),
        )
    row: dict[str, Any] = await repos_repo.register(req.repo, notes=req.notes, forge=forge)
    return row


@router.get("/repos/{full_repo:path}")
async def get_repo(full_repo: str, request: Request) -> dict[str, Any]:
    """Unified per-repo view: registry row + which repo-scoped config
    surfaces hold state for it (values live behind their own endpoints)."""
    full = full_repo
    repos_repo = request.app.state.repos_repo
    row = await repos_repo.get(full)
    if row is None:
        raise HTTPException(status_code=404, detail="repo not registered")

    configs_repo = request.app.state.qa_configs_repo
    prefs_repo = request.app.state.repo_preferences_repo
    env_repo = request.app.state.env_repo

    config_row = await configs_repo.get(full)
    prefs = await prefs_repo.get(full)
    env_vars = await env_repo.get_repo(full)
    # ADR 0013: the machines this repo's code runs on, each with its role for the repo.
    hosts_repo = getattr(request.app.state, "hosts_repo", None)
    hosts = await hosts_repo.hosts_for_repo(full) if hosts_repo is not None else []
    return {
        **row,
        "has_qa_config": config_row is not None,
        "qa_config_updated_at": str(config_row["updated_at"]) if config_row else None,
        "qa_config_updated_by": config_row["updated_by"] if config_row else None,
        "preferences": prefs,
        "env_var_count": len(env_vars),
        "env_secret_count": sum(1 for v in env_vars if v.get("var_type") == "secret"),
        "hosts": hosts,
    }


@router.put("/repos/{full_repo:path}/active")
async def set_repo_active(full_repo: str, req: RepoActiveRequest, request: Request) -> dict[str, Any]:
    repos_repo = request.app.state.repos_repo
    row: dict[str, Any] | None = await repos_repo.set_active(full_repo, req.active)
    if row is None:
        raise HTTPException(status_code=404, detail="repo not registered")
    return row


@router.put("/repos/{full_repo:path}/docker-access", dependencies=[require_admin])
async def set_repo_docker_access(full_repo: str, req: RepoDockerAccessRequest, request: Request) -> dict[str, Any]:
    """Grant or revoke the shared Docker daemon for one repo (#522).

    A sandbox profile with ``dind_enabled`` only asks for the daemon; a
    sandbox gets it when its repo holds this grant too. The daemon also runs
    the credential proxies and every other job's sandbox, so code from a
    granted repo (its tests, compose files, Dockerfiles) can read the tokens
    those proxies hold and reach the Docker host. Hence admin-tier, audited,
    and never settable through a profile, a qa.yaml or a job.
    """
    repos_repo = request.app.state.repos_repo
    row: dict[str, Any] | None = await repos_repo.set_docker_access(full_repo, req.enabled)
    if row is None:
        raise HTTPException(status_code=404, detail="repo not registered")
    config = request.app.state.config
    await emit_audit(
        getattr(request.app.state, "db", None),
        "repo_docker_access_granted" if req.enabled else "repo_docker_access_revoked",
        actor=request_actor(request),
        details={"repo": full_repo, "docker_access": req.enabled},
        audit_log_path=getattr(config, "audit_log_path", None),
    )
    return row


@router.delete("/repos/{full_repo:path}", status_code=204, response_model=None)
async def delete_repo(full_repo: str, request: Request) -> None:
    """Hard delete: registry row + QA config (CASCADE). Env vars and
    preferences are deliberately untouched — they predate the registry and
    other repos' rows must never be collateral."""
    repos_repo = request.app.state.repos_repo
    await repos_repo.delete(full_repo)
