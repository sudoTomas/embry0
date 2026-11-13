"""Pipeline templates API — CRUD for pipeline template management."""

from typing import Any

import asyncpg
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from embry0.agents.skills import normalize_delta
from embry0.api.deps import get_templates_repo
from embry0.api.repo_allowlist import deployment_writes
from embry0.api.schemas import TemplateCreateRequest, TemplateDuplicateRequest, TemplateUpdateRequest
from embry0.api.served import ServedRoute
from embry0.api.v1._skill_writes import check_added_skills
from embry0.services.model_enablement import UnpricedModelError, ensure_models_priced
from embry0.services.slop_routing import TemplateInUse, slop_route_guard
from embry0.services.world import forget_rows
from embry0.storage.repositories.pipeline_templates import Guard, PipelineTemplatesRepository
from embry0.storage.repositories.settings_documents import SettingsDocumentsRepository
from embry0.workflows._validation import validate_graph_definition
from embry0.workflows.issue_to_pr.route_plan import node_config, step_run_type
from embry0.workflows.row_behaviour import leader_problem

logger = structlog.get_logger(__name__)

router = APIRouter(route_class=ServedRoute)
# A template is the pipeline of every repository's jobs that use it: not a key with a repository allowlist's to
# change. POST /pipelines/validate stores nothing, and stays open to it.
_TEMPLATE_WRITE = deployment_writes("change the deployment's pipeline templates")


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


async def _agent_rows(request: Request) -> list[dict[str, Any]] | None:
    """The agent_definitions rows; None when the app has no repository (tests) or the read fails."""
    repo = getattr(request.app.state, "agent_defs_repo", None)
    if repo is None:
        return None
    try:
        return list(await repo.list_all())
    except Exception:
        logger.warning("template_agent_types_unavailable", exc_info=True)
        return None


def _job_agent_types(rows: list[dict[str, Any]] | None) -> set[str] | None:
    """The types a job can run now (``runs_in_jobs``: the brief writer's voices are no step); None without
    the rows, so the builtin types decide, as at plan time (plan_route)."""
    if rows is None:
        return None
    return {str(a["type"]) for a in rows if a.get("runs_in_jobs", True)}


async def _known_agent_types(request: Request) -> set[str] | None:
    """The agent_definitions types a job can run now; None when they cannot be read (``_job_agent_types``)."""
    return _job_agent_types(await _agent_rows(request))


def _no_shell_step_warnings(rows: list[dict[str, Any]] | None, graph: dict[str, Any] | None) -> list[str]:
    """A warning for each step whose tools leave its agent without an MCP server, its browser included.

    A step's ``tools`` replace its agent's (``route_plan``), and a server
    that runs a command reaches only an agent whose tools, as the operator
    gave them, name a shell tool (``embry0.agents.mcp_servers``). The agent
    is the row the step runs as (``step_run_type``); a QA step's also runs
    with the browser the QA node adds, the default backend's here, since a
    repository picks its own. Without the rows, nothing is said.
    """
    from embry0.api.v1._server_warnings import no_shell_server_warning, servers_run_without
    from embry0.workflows.qa.browser_backends import DEFAULT_BROWSER_BACKEND, backend_for, with_browser_backend

    agents = {str(row.get("type")): row for row in rows or []}
    warnings: list[str] = []
    for node in (graph or {}).get("nodes") or []:
        step_tools = node_config(node).get("tools")
        if step_tools is None:
            continue
        run_type = step_run_type(str(node.get("agent_type") or ""))
        agent = agents.get(run_type)
        if agent is None:
            continue
        if run_type == "qa":
            agent = with_browser_backend(agent, backend_for(DEFAULT_BROWSER_BACKEND))
        tools = list(step_tools) if isinstance(step_tools, list) else []
        names = servers_run_without(run_type, tools, agent.get("mcp_servers"))
        if names:
            warnings.append(
                no_shell_server_warning(
                    names, tools=f"node {node.get('node_id')!r}: the step's tools", runs=f"its {run_type} agent"
                )
            )
    return warnings


#: The template fields whose explicit null is a write (it clears the column); every other field's null means
#: "unchanged", and a null for one of :data:`_NOT_NULL` is refused.
_CLEARABLE = ("default_for_kind", "job_kind", "leader_agent")
_NOT_NULL = ("fixed_route", "offered_to_triage", "delivers")


def _reject_unknown_leader(rows: list[dict[str, Any]] | None, leader: str | None) -> None:
    """A ``leader_agent`` that names no agent a job runs is a 422 naming the field (#167)."""
    problem = leader_problem(leader, _job_agent_types(rows))
    if problem:
        raise HTTPException(status_code=422, detail=problem)


def _fixed_route_warnings(fixed: bool, graph: dict[str, Any] | None) -> list[str]:
    """What a fixed route skips: a job that names it is not triaged, and only triage asks for QA, so its qa
    step never runs."""
    if not fixed or not any(n.get("agent_type") == "qa" for n in (graph or {}).get("nodes") or []):
        return []
    return [
        "fixed_route: a job that names this template skips triage, and only triage asks for QA, "
        "so its qa step never runs"
    ]


def _route_guard(request: Request) -> Guard | None:
    """The guard a template's change runs: a template a settings document routes slop jobs to (#169) is not
    deleted, renamed or made to stop taking a code job. None without the settings store (tests)."""
    store = getattr(request.app.state, "settings", None)
    if store is None:
        return None
    return slop_route_guard(SettingsDocumentsRepository(store.db), store.catalog)


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


@router.post("/pipelines/templates", status_code=201, dependencies=[_TEMPLATE_WRITE])
async def create_template(
    req: TemplateCreateRequest,
    request: Request,
    repo: PipelineTemplatesRepository = Depends(get_templates_repo),
) -> dict[str, Any]:
    # RAV-601: templates are runtime control-flow now — only executable
    # graphs (single linear chain of known agent types) may save.
    rows = await _agent_rows(request)
    problems = validate_graph_definition(req.name, req.graph_definition, _job_agent_types(rows))
    if problems:
        raise HTTPException(status_code=422, detail="; ".join(problems))
    _reject_unpriced(req.graph_definition)
    _reject_unknown_leader(rows, req.leader_agent)
    warnings = await _check_template_skills(request, req.graph_definition)
    warnings += _no_shell_step_warnings(rows, req.graph_definition)
    warnings += _fixed_route_warnings(req.fixed_route, req.graph_definition)
    try:
        created = await repo.create(
            name=req.name,
            description=req.description,
            graph_definition=req.graph_definition,
            sandbox_profile=req.sandbox_profile,
            default_for_kind=req.default_for_kind,
            fixed_route=req.fixed_route,
            offered_to_triage=req.offered_to_triage,
            job_kind=req.job_kind,
            delivers=req.delivers,
            leader_agent=req.leader_agent,
        )
    except asyncpg.UniqueViolationError as exc:
        # The server names the constraint it refused; the body cannot tell the two apart.
        if exc.constraint_name == _DEFAULT_KIND_INDEX:
            raise _default_kind_taken(req.default_for_kind) from None
        raise _template_name_taken(req.name) from None
    forget_rows(request.app.state, "templates")
    return {**created, "warnings": warnings}


@router.put("/pipelines/templates/{template_id}", dependencies=[_TEMPLATE_WRITE])
async def update_template(
    template_id: str,
    req: TemplateUpdateRequest,
    request: Request,
    repo: PipelineTemplatesRepository = Depends(get_templates_repo),
) -> dict[str, Any]:
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    # Only these fields' null is a write: default_for_kind's clears the default
    # so another template can take the kind, job_kind's and leader_agent's
    # clear theirs (#167). Every other field's null means "unchanged", but the
    # behaviour columns are NOT NULL: a null sent for one is refused.
    sent = req.model_dump()
    for key in _NOT_NULL:
        if key in req.model_fields_set and sent[key] is None:
            raise HTTPException(status_code=422, detail=f"{key} cannot be null")
    for key in _CLEARABLE:
        if key in req.model_fields_set and sent[key] is None:
            updates[key] = None
    if not updates:
        template = await repo.get(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")
        return template
    # 1. The body on its own: graph validity, deferred tools, priced models, skills delta shape.
    graph = updates.get("graph_definition")
    added: list[str] = []
    rows: list[dict[str, Any]] | None = None
    if "graph_definition" in updates:
        rows = await _agent_rows(request)
        problems = validate_graph_definition(str(template_id), updates["graph_definition"], _job_agent_types(rows))
        if problems:
            raise HTTPException(status_code=422, detail="; ".join(problems))
    _reject_unpriced(graph)
    if "graph_definition" in updates:
        added = _added_skills(graph)
    # 2. The template must exist: the added names are measured against it.
    stored = await repo.get(template_id) if repo is not None else None
    if stored is None:
        raise HTTPException(status_code=404, detail="Template not found")
    # Only a leader the write changes is checked: the dialog sends every field, and a stored leader that
    # no longer runs in jobs must not stop the rest from saving.
    if updates.get("leader_agent") is not None and updates["leader_agent"] != stored.get("leader_agent"):
        rows = rows if rows is not None else await _agent_rows(request)
        _reject_unknown_leader(rows, updates["leader_agent"])
    # 3. Only the names this save adds are checked; names the stored template has keep working.
    already = _stored_added_skills(stored)
    warnings = await check_added_skills(request, [s for s in added if s not in already])
    warnings += _no_shell_step_warnings(rows, graph)
    if "fixed_route" in updates or "graph_definition" in updates:
        warnings += _fixed_route_warnings(
            bool(updates.get("fixed_route", stored.get("fixed_route"))),
            updates.get("graph_definition", stored.get("graph_definition")),
        )
    try:
        updated = await repo.update(template_id, guard=_route_guard(request), **updates)
    except TemplateInUse as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except asyncpg.UniqueViolationError as exc:
        if exc.constraint_name == _DEFAULT_KIND_INDEX:
            raise _default_kind_taken(updates.get("default_for_kind")) from None
        raise _template_name_taken(updates.get("name")) from None
    if not updated:
        raise HTTPException(status_code=404, detail="Template not found")
    forget_rows(request.app.state, "templates")
    if "name" in updates and updates["name"] != stored.get("name"):
        # The rename carried each agent's guild task with it (the repository's update).
        forget_rows(request.app.state, "agents")
    return {**updated, "warnings": warnings}


@router.delete("/pipelines/templates/{template_id}", dependencies=[_TEMPLATE_WRITE])
async def delete_template(
    template_id: str,
    request: Request,
    repo: PipelineTemplatesRepository = Depends(get_templates_repo),
) -> dict[str, Any]:
    try:
        await repo.delete(template_id, guard=_route_guard(request))
    except TemplateInUse as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    forget_rows(request.app.state, "templates")
    return {"id": template_id, "status": "deleted"}


@router.post("/pipelines/templates/{template_id}/duplicate", status_code=201, dependencies=[_TEMPLATE_WRITE])
async def duplicate_template(
    template_id: str,
    req: TemplateDuplicateRequest,
    request: Request,
    repo: PipelineTemplatesRepository = Depends(get_templates_repo),
) -> dict[str, Any]:
    try:
        copy = await repo.duplicate(template_id, new_name=req.name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except asyncpg.UniqueViolationError:
        raise _template_name_taken(req.name) from None
    forget_rows(request.app.state, "templates")
    return copy


@router.post("/pipelines/validate")
async def validate_pipeline(graph: dict[str, Any], request: Request) -> dict[str, Any]:
    # Delegates to the same validator the save path uses so the frontend
    # editor gets identical diagnostics (RAV-601).
    errors = validate_graph_definition(graph.get("name") or "pipeline", graph, await _known_agent_types(request))
    return {"valid": len(errors) == 0, "errors": errors}
