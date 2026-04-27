"""Pipeline templates API — CRUD for pipeline template management."""

from fastapi import APIRouter, Depends, HTTPException

from athanor.api.deps import get_templates_repo
from athanor.api.schemas import TemplateCreateRequest, TemplateDuplicateRequest, TemplateUpdateRequest
from athanor.storage.repositories.pipeline_templates import PipelineTemplatesRepository

router = APIRouter()


@router.get("/pipelines/templates")
async def list_templates(
    repo: PipelineTemplatesRepository = Depends(get_templates_repo),
) -> list[dict]:
    return await repo.list_all()


@router.get("/pipelines/templates/{template_id}")
async def get_template(
    template_id: str,
    repo: PipelineTemplatesRepository = Depends(get_templates_repo),
) -> dict:
    template = await repo.get(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.post("/pipelines/templates", status_code=201)
async def create_template(
    req: TemplateCreateRequest,
    repo: PipelineTemplatesRepository = Depends(get_templates_repo),
) -> dict:
    return await repo.create(
        name=req.name,
        description=req.description,
        graph_definition=req.graph_definition,
        agent_models=req.agent_models,
        sandbox_profile=req.sandbox_profile,
    )


@router.put("/pipelines/templates/{template_id}")
async def update_template(
    template_id: str,
    req: TemplateUpdateRequest,
    repo: PipelineTemplatesRepository = Depends(get_templates_repo),
) -> dict:
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        template = await repo.get(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")
        return template
    return await repo.update(template_id, **updates)


@router.delete("/pipelines/templates/{template_id}")
async def delete_template(
    template_id: str,
    repo: PipelineTemplatesRepository = Depends(get_templates_repo),
) -> dict:
    await repo.delete(template_id)
    return {"id": template_id, "status": "deleted"}


@router.post("/pipelines/templates/{template_id}/duplicate", status_code=201)
async def duplicate_template(
    template_id: str,
    req: TemplateDuplicateRequest,
    repo: PipelineTemplatesRepository = Depends(get_templates_repo),
) -> dict:
    try:
        return await repo.duplicate(template_id, new_name=req.name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/pipelines/validate")
async def validate_pipeline(graph: dict) -> dict:
    errors: list[str] = []
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    if not nodes:
        errors.append("Pipeline must have at least one node")
    node_ids = {n.get("node_id") for n in nodes}
    for edge in edges:
        if edge.get("source") not in node_ids:
            errors.append(f"Edge source '{edge.get('source')}' not found in nodes")
        if edge.get("target") not in node_ids:
            errors.append(f"Edge target '{edge.get('target')}' not found in nodes")
    return {"valid": len(errors) == 0, "errors": errors}
