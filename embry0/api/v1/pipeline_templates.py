"""Pipeline templates API — CRUD for pipeline template management."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from embry0.api.deps import get_templates_repo
from embry0.api.schemas import TemplateCreateRequest, TemplateDuplicateRequest, TemplateUpdateRequest
from embry0.storage.repositories.pipeline_templates import PipelineTemplatesRepository
from embry0.workflows._validation import validate_graph_definition

router = APIRouter()


@router.get("/pipelines/templates")
async def list_templates(
    repo: PipelineTemplatesRepository = Depends(get_templates_repo),
) -> list[dict[str, Any]]:
    return await repo.list_all()


@router.get("/pipelines/templates/{template_id}")
async def get_template(
    template_id: str,
    repo: PipelineTemplatesRepository = Depends(get_templates_repo),
) -> dict[str, Any]:
    template = await repo.get(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.post("/pipelines/templates", status_code=201)
async def create_template(
    req: TemplateCreateRequest,
    repo: PipelineTemplatesRepository = Depends(get_templates_repo),
) -> dict[str, Any]:
    # RAV-601: templates are runtime control-flow now — only executable
    # graphs (single linear chain of known agent types) may save.
    problems = validate_graph_definition(req.name, req.graph_definition)
    if problems:
        raise HTTPException(status_code=422, detail="; ".join(problems))
    return await repo.create(
        name=req.name,
        description=req.description,
        graph_definition=req.graph_definition,
        agent_models=req.agent_models,
        sandbox_profile=req.sandbox_profile,
        default_for_kind=req.default_for_kind,
    )


@router.put("/pipelines/templates/{template_id}")
async def update_template(
    template_id: str,
    req: TemplateUpdateRequest,
    repo: PipelineTemplatesRepository = Depends(get_templates_repo),
) -> dict[str, Any]:
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        template = await repo.get(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")
        return template
    if "graph_definition" in updates:
        problems = validate_graph_definition(str(template_id), updates["graph_definition"])
        if problems:
            raise HTTPException(status_code=422, detail="; ".join(problems))
    return await repo.update(template_id, **updates)


@router.delete("/pipelines/templates/{template_id}")
async def delete_template(
    template_id: str,
    repo: PipelineTemplatesRepository = Depends(get_templates_repo),
) -> dict[str, Any]:
    await repo.delete(template_id)
    return {"id": template_id, "status": "deleted"}


@router.post("/pipelines/templates/{template_id}/duplicate", status_code=201)
async def duplicate_template(
    template_id: str,
    req: TemplateDuplicateRequest,
    repo: PipelineTemplatesRepository = Depends(get_templates_repo),
) -> dict[str, Any]:
    try:
        return await repo.duplicate(template_id, new_name=req.name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/pipelines/validate")
async def validate_pipeline(graph: dict[str, Any]) -> dict[str, Any]:
    # Delegates to the same validator the save path uses so the frontend
    # editor gets identical diagnostics (RAV-601).
    errors = validate_graph_definition(graph.get("name") or "pipeline", graph)
    return {"valid": len(errors) == 0, "errors": errors}
