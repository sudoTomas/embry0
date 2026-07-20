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

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from embry0.audit.helpers import emit_audit

router = APIRouter()


class QAImageBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str = Field(min_length=3, pattern=r"^[^/\s]+/[^/\s]+$")
    branch: str = Field(default="main", min_length=1)
    force: bool = False


@router.post("/qa/images/build")
async def build_qa_cache_image(req: QAImageBuildRequest, request: Request) -> dict[str, str]:
    state = request.app.state
    deps = {
        "sandbox_mgr": getattr(state, "sandbox_manager", None),
        "docker": getattr(state, "docker", None),
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

    try:
        outcome = await run_build_qa_image(
            repo=req.repo,
            branch=req.branch,
            force=req.force,
            deps=deps,
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
        actor=request.client.host if request.client else "api",
        details={"repo": req.repo, "branch": req.branch, "force": req.force, "outcome": outcome},
        audit_log_path=config.audit_log_path,
    )
    return {"repo": req.repo, "branch": req.branch, "status": outcome}
