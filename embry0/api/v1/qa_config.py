"""Per-repo QA config endpoints — DB-backed (RAV-1251, ex EMB-48 file store).

GET returns the stored qa.yaml for a repo (404 when absent — the DB is the
ONLY source; RAV-1253 removed the legacy file-store/in-repo channels).
PUT schema-validates the body as qa.yaml v2 and upserts the DB row
(auto-registering the repo) — the manual counterpart of the onboard
pipeline's write. DELETE removes the config row; the repo stays registered.
Both writes are recorded in the repo's history (ADR 0019) and audited.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from embry0.api.deps import request_actor
from embry0.audit.actions import AuditAction
from embry0.audit.helpers import emit_audit

router = APIRouter()


async def _audit(request: Request, action: AuditAction, details: dict[str, Any]) -> None:
    config = getattr(request.app.state, "config", None)
    await emit_audit(
        getattr(request.app.state, "db", None),
        action,
        actor=request_actor(request),
        details=details,
        audit_log_path=getattr(config, "audit_log_path", None),
    )


@router.get("/repos/{full_repo:path}/qa-config")
async def get_qa_config(full_repo: str, request: Request) -> Response:
    configs_repo = request.app.state.qa_configs_repo
    row = await configs_repo.get(full_repo)
    if row is None:
        raise HTTPException(status_code=404, detail="no QA config stored for this repo")
    return Response(content=row["yaml_text"], media_type="application/yaml")


@router.put("/repos/{full_repo:path}/qa-config")
async def put_qa_config(full_repo: str, request: Request) -> dict[str, Any]:
    from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

    text = (await request.body()).decode("utf-8", errors="replace")
    if not text.strip():
        raise HTTPException(status_code=400, detail="empty body — send the qa.yaml v2 text")
    try:
        parse_qa_yaml_v2(text)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"qa.yaml v2 validation failed: {exc}") from exc
    configs_repo = request.app.state.qa_configs_repo
    try:
        row, revision_id = await configs_repo.upsert_recorded(full_repo, text, updated_by=request_actor(request))
    except ValueError as exc:
        # Spec 1A §3.8: on a deployment that refuses to guess a forge, the
        # auto-registration this PUT performs would have to invent one.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _audit(request, "repo.qa_config_set", {"repo": full_repo, "revision_id": revision_id})
    # revision_id is None when the text was unchanged: nothing new to undo.
    return {"repo": full_repo, "updated_at": str(row["updated_at"]), "revision_id": revision_id}


@router.delete("/repos/{full_repo:path}/qa-config", status_code=204, response_model=None)
async def delete_qa_config(full_repo: str, request: Request) -> None:
    configs_repo = request.app.state.qa_configs_repo
    revision_id = await configs_repo.delete_recorded(full_repo, actor=request_actor(request))
    if revision_id is not None:
        await _audit(request, "repo.qa_config_deleted", {"repo": full_repo, "revision_id": revision_id})
