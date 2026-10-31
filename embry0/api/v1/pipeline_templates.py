"""Pipeline templates API — CRUD for pipeline template management."""

from typing import Any

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from embry0.agents.skills import normalize_delta
from embry0.api.deps import get_templates_repo
from embry0.api.schemas import TemplateCreateRequest, TemplateDuplicateRequest, TemplateUpdateRequest
from embry0.api.v1._skill_writes import check_added_skills
from embry0.services.model_enablement import UnpricedModelError, ensure_models_priced
from embry0.storage.repositories.pipeline_templates import PipelineTemplatesRepository
from embry0.workflows._validation import validate_graph_definition
from embry0.workflows.issue_to_pr.route_plan import node_config

logger = structlog.get_logger(__name__)

router = APIRouter()


def _reject_unpriced(graph: dict[str, Any] | None) -> None:
    """#219: a template's models — one per step, on ``config.model`` (#468) or a
    legacy top-level ``model`` (#78) — must not run unmetered.

    Save-time only: ``validate_graph_definition`` also runs in plan_route, where
    a problem silently skips the template, and the resolver refuses an unpriced
    model at run time anyway.
    """
    nodes = (graph or {}).get("nodes") or []
    step_models = [str(node_config(n).get("model") or "") for n in nodes]
    try:
        ensure_models_priced(step_models)
    except UnpricedModelError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _added_skills(graph: dict[str, Any] | None) -> list[str]:
    """Every name the graph's nodes add; a malformed skills delta is a 422."""
    added: list[str] = []
    for n in (graph or {}).get("nodes") or []:
        skills = node_config(n).get("skills")
        if skills is None:
            continue
        try:
            added.extend(normalize_delta(skills)["add"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"node {n.get('node_id')!r}: {exc}") from exc
    return added


def _stored_added_skills(template: dict[str, Any] | None) -> set[str]:
    """The names a stored template already adds; a node whose delta no longer parses adds none."""
    names: set[str] = set()
    for n in ((template or {}).get("graph_definition") or {}).get("nodes") or []:
        try:
            names.update(normalize_delta(node_config(n).get("skills"))["add"])
        except ValueError:
            continue
    return names


# migration 42: the partial unique index behind "at most one default per kind".
_DEFAULT_KIND_INDEX = "idx_pipeline_templates_default_kind"


def _default_kind_taken(kind: str | None) -> HTTPException:
    """The partial unique index on ``default_for_kind`` refused a second holder."""
    return HTTPException(status_code=409, detail=f"another template is already the default for {kind}; clear it first")


def _template_name_taken(name: str | None) -> HTTPException:
    """``pipeline_templates.name`` is UNIQUE; the only other constraint a template write can violate."""
    return HTTPException(status_code=409, detail=f"a template named {name} already exists")


async def _known_agent_types(request: Request) -> set[str] | None:
    """The agent_definitions types a job can run now (``runs_in_jobs``: the brief writer's voices are no step);
    None when the app has no repository (tests) or the read fails, so the builtin types decide, as at plan time
    (plan_route)."""
    repo = getattr(request.app.state, "agent_defs_repo", None)
    if repo is None:
        return None
    try:
        return {str(a["type"]) for a in await repo.list_all() if a.get("runs_in_jobs", True)}
    except Exception:
        logger.warning("template_agent_types_unavailable", exc_info=True)
        return None


async def _check_template_skills(request: Request, graph: dict[str, Any] | None) -> list[str]:
    """Check every name the graph adds; return the warnings."""
    return await check_added_skills(request, _added_skills(graph))


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
    request: Request,
    repo: PipelineTemplatesRepository = Depends(get_templates_repo),
) -> dict[str, Any]:
    # RAV-601: templates are runtime control-flow now — only executable
    # graphs (single linear chain of known agent types) may save.
    problems = validate_graph_definition(req.name, req.graph_definition, await _known_agent_types(request))
    if problems:
        raise HTTPException(status_code=422, detail="; ".join(problems))
    _reject_unpriced(req.graph_definition)
    warnings = await _check_template_skills(request, req.graph_definition)
    try:
        created = await repo.create(
            name=req.name,
            description=req.description,
            graph_definition=req.graph_definition,
            sandbox_profile=req.sandbox_profile,
            default_for_kind=req.default_for_kind,
        )
    except asyncpg.UniqueViolationError as exc:
        # The server names the constraint it refused; the body cannot tell the two apart.
        if exc.constraint_name == _DEFAULT_KIND_INDEX:
            raise _default_kind_taken(req.default_for_kind) from None
        raise _template_name_taken(req.name) from None
    return {**created, "warnings": warnings}


@router.put("/pipelines/templates/{template_id}")
async def update_template(
    template_id: str,
    req: TemplateUpdateRequest,
    request: Request,
    repo: PipelineTemplatesRepository = Depends(get_templates_repo),
) -> dict[str, Any]:
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    # Only this field's null is a write: it clears the default so another
    # template can take the kind. Every other field's null means "unchanged".
    if "default_for_kind" in req.model_fields_set and req.default_for_kind is None:
        updates["default_for_kind"] = None
    if not updates:
        template = await repo.get(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")
        return template
    # 1. The body on its own: graph validity, deferred tools, priced models, skills delta shape.
    graph = updates.get("graph_definition")
    added: list[str] = []
    if "graph_definition" in updates:
        problems = validate_graph_definition(
            str(template_id), updates["graph_definition"], await _known_agent_types(request)
        )
        if problems:
            raise HTTPException(status_code=422, detail="; ".join(problems))
    _reject_unpriced(graph)
    if "graph_definition" in updates:
        added = _added_skills(graph)
    # 2. The template must exist: the added names are measured against it.
    stored = await repo.get(template_id) if repo is not None else None
    if stored is None:
        raise HTTPException(status_code=404, detail="Template not found")
    # 3. Only the names this save adds are checked; names the stored template has keep working.
    already = _stored_added_skills(stored)
    warnings = await check_added_skills(request, [s for s in added if s not in already])
    try:
        updated = await repo.update(template_id, **updates)
    except asyncpg.UniqueViolationError as exc:
        if exc.constraint_name == _DEFAULT_KIND_INDEX:
            raise _default_kind_taken(updates.get("default_for_kind")) from None
        raise _template_name_taken(updates.get("name")) from None
    if not updated:
        raise HTTPException(status_code=404, detail="Template not found")
    return {**updated, "warnings": warnings}


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
    except asyncpg.UniqueViolationError:
        raise _template_name_taken(req.name) from None


@router.post("/pipelines/validate")
async def validate_pipeline(graph: dict[str, Any], request: Request) -> dict[str, Any]:
    # Delegates to the same validator the save path uses so the frontend
    # editor gets identical diagnostics (RAV-601).
    errors = validate_graph_definition(graph.get("name") or "pipeline", graph, await _known_agent_types(request))
    return {"valid": len(errors) == 0, "errors": errors}
