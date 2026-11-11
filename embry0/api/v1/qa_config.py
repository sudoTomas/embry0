"""Per-repo QA config endpoints — DB-backed (RAV-1251, ex EMB-48 file store).

GET returns the stored qa.yaml for a repo (404 when absent — the DB is the
ONLY source; RAV-1253 removed the legacy file-store/in-repo channels).
PUT schema-validates the body as qa.yaml v2 and upserts the DB row
(auto-registering the repo) — the manual counterpart of the onboard
pipeline's write. DELETE removes the config row; the repo stays registered.
Both writes are recorded in the repo's history (ADR 0019) and audited.
POST .../check runs the free check (services/qa_config_check.py) on a
document without storing it.

``cache.turbo_remote`` is the deployment's to turn on, not the repository's
(:mod:`embry0.services.shared_build_cache`). A key with a repository
allowlist keeps it on where an admin turned it on and may turn it off, but
never turns it on: not by this PUT, nor by applying a setup proposal or
undoing a change. The check runs on the stored setup with its row held, so a
setup saved meanwhile is the one checked.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException, Request, Response

from embry0.api.deps import request_actor
from embry0.api.repo_allowlist import (
    enforce_deployment_allowlist,
    enforce_repo_allowlist,
    key_covers,
    restricted_key_name,
)
from embry0.audit.actions import AuditAction
from embry0.audit.helpers import emit_audit
from embry0.services.qa_config_check import check_qa_config
from embry0.services.shared_build_cache import SharedBuildCacheRefused, keeps_the_shared_cache
from embry0.storage.repositories.repo_qa_configs import SetupChanged
from embry0.workflows.qa.qa_yaml_errors import plain_errors

router = APIRouter()


#: What a refused write would have done, in the refusal.
TURN_ON_THE_SHARED_CACHE = "turn on the shared build cache for a repository"


def shared_cache_check(request: Request, text: str) -> Callable[[str | None], None] | None:
    """The check a write of the setup ``text`` hands to the store for the caller (``upsert_recorded``'s ``check``):
    for a key with an allowlist, that it does not turn the shared build cache on; None for anyone else."""
    return keeps_the_shared_cache(text) if restricted_key_name(request) is not None else None


async def refuse_the_shared_cache(request: Request) -> NoReturn:
    """Refuse, log and audit a key's write that would turn the shared build cache on, as the deployment's."""
    await enforce_deployment_allowlist(request, action=TURN_ON_THE_SHARED_CACHE)
    raise AssertionError("only a key with an allowlist is checked")  # enforce_deployment_allowlist raised


async def _audit(request: Request, action: AuditAction, details: dict[str, Any]) -> None:
    config = getattr(request.app.state, "config", None)
    await emit_audit(
        getattr(request.app.state, "db", None),
        action,
        actor=request_actor(request),
        details=details,
        audit_log_path=getattr(config, "audit_log_path", None),
    )


@router.get("/repos/{full_repo:repo}/-/qa-config")
async def get_qa_config(full_repo: str, request: Request) -> Response:
    configs_repo = request.app.state.qa_configs_repo
    # A repository the caller's key does not cover has nothing stored for it (embry0.api.repository_reads).
    row = await configs_repo.get(full_repo) if key_covers(request, full_repo) else None
    if row is None:
        raise HTTPException(status_code=404, detail="no QA config stored for this repo")
    return Response(content=row["yaml_text"], media_type="application/yaml")


@router.put("/repos/{full_repo:repo}/-/qa-config")
async def put_qa_config(full_repo: str, request: Request) -> dict[str, Any]:
    await enforce_repo_allowlist(request, full_repo, action="change a repository's test setup")
    from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

    text = (await request.body()).decode("utf-8", errors="replace")
    if not text.strip():
        raise HTTPException(status_code=400, detail="empty body — send the qa.yaml v2 text")
    try:
        parse_qa_yaml_v2(text)
    except Exception as exc:  # noqa: BLE001
        detail = "\n".join(plain_errors(exc)) + "\n\nTechnical details:\n" + str(exc)
        raise HTTPException(status_code=422, detail=detail) from exc
    configs_repo = request.app.state.qa_configs_repo
    try:
        row, revision_id = await configs_repo.upsert_recorded(
            full_repo, text, updated_by=request_actor(request), check=shared_cache_check(request, text)
        )
    except SharedBuildCacheRefused:
        await refuse_the_shared_cache(request)
    except SetupChanged:
        # Checked against no setup, while another's first one was being saved.
        raise HTTPException(status_code=409, detail="the test setup was saved meanwhile; read it again") from None
    except ValueError as exc:
        # Spec 1A §3.8: on a deployment that refuses to guess a forge, the
        # auto-registration this PUT performs would have to invent one.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _audit(request, "repo.qa_config_set", {"repo": full_repo, "revision_id": revision_id})
    # revision_id is None when the text was unchanged: nothing new to undo.
    return {"repo": full_repo, "updated_at": str(row["updated_at"]), "revision_id": revision_id}


@router.delete("/repos/{full_repo:repo}/-/qa-config", status_code=204, response_model=None)
async def delete_qa_config(full_repo: str, request: Request) -> None:
    await enforce_repo_allowlist(request, full_repo, action="change a repository's test setup")
    configs_repo = request.app.state.qa_configs_repo
    revision_id = await configs_repo.delete_recorded(full_repo, actor=request_actor(request))
    if revision_id is not None:
        await _audit(request, "repo.qa_config_deleted", {"repo": full_repo, "revision_id": revision_id})


@router.post("/repos/{full_repo:repo}/-/qa-config/check")
async def check_repo_qa_config(full_repo: str, request: Request) -> dict[str, Any]:
    """Check a test setup the way a run would use it. Stores nothing.

    A repository the caller's key does not cover is checked as one nobody
    registered: no variables of its own and no Docker access
    (embry0.api.repository_reads)."""
    text = (await request.body()).decode("utf-8", errors="replace")
    if not text.strip():
        raise HTTPException(status_code=400, detail="empty body — send the qa.yaml v2 text")
    state = request.app.state
    reads = key_covers(request, full_repo)
    env_keys = {str(v["key"]) for v in await state.env_repo.get_global()}
    env_keys |= {str(v["key"]) for v in (await state.env_repo.get_repo(full_repo) if reads else [])}
    return await check_qa_config(
        text,
        profiles_repo=state.profiles_repo,
        env_keys=env_keys,
        docker_access=await state.repos_repo.docker_access(full_repo) if reads else False,
    )
