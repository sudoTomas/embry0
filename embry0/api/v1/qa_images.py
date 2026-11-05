"""Pre-baked QA cache-image builds (EMB-42).

``POST /qa/images/build`` wires ``run_build_qa_image`` (the unit-tested
core in ``embry0/cache/image_builder_cli.py``) to the app's live
dependencies. This is the endpoint the ``embry0 build-qa-image`` CLI
calls — the build requires the running stack (DinD daemon, git-proxy,
Postgres), so the orchestrator is the only sane place to execute it.

The build is synchronous: a fresh build (clone + npm ci + turbo build +
docker commit) takes minutes, so callers must use a generous HTTP
timeout. Idempotent unless ``force`` — a matching ``lockfile_sha`` on
the active tag short-circuits to ``skipped``.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from embry0.api.deps import ensure_admin, request_actor
from embry0.api.repo_allowlist import enforce_repo_allowlist
from embry0.api.schemas.repo_path import validate_repo_path
from embry0.audit.helpers import emit_audit
from embry0.safety.error_codes import ErrorCode
from embry0.services.forge_resolver import resolve_forge
from embry0.storage.repositories.repos import UnregisteredRepoError

logger = structlog.get_logger(__name__)

router = APIRouter()


class QAImageBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    branch: str = Field(default="main", min_length=1)
    force: bool = False

    @field_validator("repo")
    @classmethod
    def _repo_shape(cls, v: str) -> str:
        return validate_repo_path(v)


async def _forge_default_branch(request: Request, repo: str, forge: str) -> str | None:
    """The repository's default branch as its forge reports it; None when the forge did not answer.

    The same source a QA run uses to select a pre-built image
    (``embry0.cache.trusted_run``): the forge, never a job's or a
    preference's ``base_branch``.
    """
    from embry0.services.base_branch import forge_default_branch

    state = request.app.state
    config = state.config
    resolver = getattr(state, "credential_resolver", None)
    if forge == "gitlab":
        from embry0.execution.gitlab_client import make_gitlab_client

        try:
            gitlab = make_gitlab_client(config, credential_resolver=resolver)
        except Exception:  # noqa: BLE001 — no client means no answer: admin tier
            logger.warning("qa_image_build_forge_client_failed", repo=repo, forge=forge, exc_info=True)
            return None
        return await forge_default_branch(repo=repo, forge=forge, gitlab_client=gitlab)
    from embry0.execution.github_client import GitHubClient

    github = GitHubClient(default_token=getattr(config, "github_token", "") or "", credential_resolver=resolver)
    return await forge_default_branch(repo=repo, forge=forge, github_client=github)


@router.post("/qa/images/build")
async def build_qa_cache_image(req: QAImageBuildRequest, request: Request) -> dict[str, str]:
    await enforce_repo_allowlist(request, req.repo, action="build a QA cache image")
    state = request.app.state
    # Spec 3.6: bake on the repo's own forge. An unregistered repo on a
    # deployment with no DEFAULT_FORGE is refused here rather than baking an
    # image from whatever github.com happens to serve at that path.
    try:
        forge = await resolve_forge(getattr(state, "repos_repo", None), req.repo)
    except UnregisteredRepoError as exc:
        raise HTTPException(status_code=422, detail=f"{ErrorCode.REPO_UNREGISTERED}: {exc}") from exc
    # A prebaked image becomes the repository's QA base, so baking from any
    # branch but the forge's default branch is admin tier, and so is a bake
    # when the forge does not answer. A default-branch bake stays at operator
    # tier (the router's require_auth).
    default_branch = await _forge_default_branch(request, req.repo, forge)
    if req.branch != default_branch:
        await ensure_admin(request)
    deps = {
        "sandbox_mgr": getattr(state, "sandbox_manager", None),
        "runtime": getattr(state, "runtime", None),
        "profiles_repo": getattr(state, "profiles_repo", None),
        "proxy_mgr": getattr(state, "proxy_manager", None),
        "image_repo": getattr(state, "qa_image_tags_repo", None),
    }
    missing = [k for k, v in deps.items() if v is None]
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"image-build dependencies not initialized: {', '.join(missing)}",
        )

    from embry0.cache.image_builder import ImageBuildError
    from embry0.cache.image_builder_cli import run_build_qa_image

    # RAV-1400: honor the repo's stored cache.install_command (e.g. a
    # repo that needs `npm ci --legacy-peer-deps`). Fail-open: no stored
    # config / unparseable config → builder default.
    install_command: str | None = None
    configs_repo = getattr(state, "qa_configs_repo", None)
    if configs_repo is not None:
        try:
            stored = await configs_repo.get(req.repo)
            if stored and stored.get("yaml_text"):
                from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

                install_command = parse_qa_yaml_v2(stored["yaml_text"]).cache.install_command
        except Exception:  # noqa: BLE001 — a bad stored config must not block a bake
            install_command = None

    try:
        outcome = await run_build_qa_image(
            repo=req.repo,
            branch=req.branch,
            force=req.force,
            deps=deps,
            install_command=install_command,
            image_registry=getattr(state.config, "image_registry", "") or "",
            forge=forge,
            gitlab_url=getattr(state.config, "gitlab_url", "") or "",
        )
    except ImageBuildError as exc:
        raise HTTPException(status_code=502, detail=f"image build failed: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — build infra faults (bad repo, docker/clone
        # failures) raise plain RuntimeError from the docker layer; a raw 500
        # tells the operator nothing. Same 502 mapping, message surfaced.
        raise HTTPException(status_code=502, detail=f"image build failed: {exc}") from exc

    config = state.config
    await emit_audit(
        getattr(state, "db", None),
        "qa_image_build",
        actor=request_actor(request),
        details={"repo": req.repo, "branch": req.branch, "force": req.force, "outcome": outcome},
        audit_log_path=config.audit_log_path,
    )
    return {"repo": req.repo, "branch": req.branch, "status": outcome}
