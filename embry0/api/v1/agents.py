"""Agent definitions API — CRUD for agent types."""

from typing import Any, Final

from fastapi import APIRouter, Depends, HTTPException, Request

from embry0.agents.skills import is_repository_skill, skill_source, triage_may_add, triage_skill_choices
from embry0.api.deps import get_agent_defs_repo, require_admin
from embry0.api.repo_allowlist import deployment_writes
from embry0.api.schemas import AgentCreateRequest, AgentUpdateRequest
from embry0.api.v1._audit import audit
from embry0.api.v1._skill_writes import check_added_skills
from embry0.services import job_briefs, proposal_briefs
from embry0.services.brief_writer import voice_problem
from embry0.services.model_enablement import (
    DisabledModelError,
    UnpricedModelError,
    ensure_models_enabled,
    ensure_models_priced,
)
from embry0.services.provider_readiness import (
    ProviderNotReadyError,
    annotate_agents,
    ensure_model_provider_ready,
)
from embry0.services.tool_catalog import unknown_tools, unknown_tools_message
from embry0.services.world import forget_rows
from embry0.storage.repositories.agent_definitions import AgentDefinitionsRepository
from embry0.workflows.row_behaviour import CODING_AGENT_TYPES, quest_template_problem, untemplated_task_problem

# Agent definitions run in every repository's jobs: not a key with a repository allowlist's to change.
router = APIRouter(dependencies=[deployment_writes("change the deployment's agents")])


async def _reject_if_disabled(request: Request, model: str | None) -> None:
    """RAV-1297: explicitly picking a disabled model is a save-time 422.

    Fail-open when provider_repo isn't wired (minimal test apps) — the
    runtime tier-fallback still covers that path.
    """
    if not model:
        return
    # #219: an unpriced provider model would run unmetered — never fail-open.
    try:
        ensure_models_priced([model])
    except UnpricedModelError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    provider_repo = getattr(request.app.state, "provider_repo", None)
    if provider_repo is not None:
        try:
            await ensure_models_enabled([model], provider_repo)
        except DisabledModelError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Opt-in (AGENT_MODEL_PROVIDER_CHECK): a model whose provider has no
    # credential would only fail once the job runs. Refuse it here, naming
    # the provider. Off by default, so an install keeps today's behavior.
    config = getattr(request.app.state, "config", None)
    if getattr(config, "agent_model_provider_check", False):
        try:
            await ensure_model_provider_ready(model, request.app.state)
        except ProviderNotReadyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


async def catalog_skill_names(request: Request) -> set[str] | None:
    """Every skill name the catalog knows; None when no catalog is wired (minimal test apps)."""
    repo = getattr(request.app.state, "skill_sources_repo", None)
    if repo is None:
        return None
    return {c["name"] for c in await repo.catalog()}


@router.get("/agents")
async def list_agents(
    request: Request,
    repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo),
) -> list[dict[str, Any]]:
    """Every agent definition, each with the provider its model routes to and
    whether that provider has a credential right now (``provider``, ``provider_ready``),
    and the model and provider a job that names no model runs it on
    (``effective_model``, ``effective_model_source``, ``effective_provider``,
    ``effective_provider_ready``)."""
    return [_with_coding(a) for a in await annotate_agents(await repo.list_all(), request.app.state)]


@router.get("/agents/{agent_type}")
async def get_agent(
    agent_type: str,
    request: Request,
    repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo),
) -> dict[str, Any]:
    agent = await repo.get(agent_type)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent type not found")
    [annotated] = await annotate_agents([agent], request.app.state)
    annotated = _with_coding(annotated)
    known = await catalog_skill_names(request)
    # A repository skill (no colon) is never in the catalog, and never an orphan.
    annotated["orphan_skills"] = (
        [s for s in annotated.get("skills") or [] if not is_repository_skill(s) and s not in known]
        if known is not None
        else []
    )
    return annotated


NO_TOOLS_WARNING = (
    "tools is empty: a job runs this agent with no tools at all, so it can read nothing and change nothing, "
    "and it finishes with text only. Name its tools to give it any."
)


def _no_tools_warnings(agent: dict[str, Any]) -> list[str]:
    """The warning an agent that jobs run gets when its tool list is empty: that is no tools, never the defaults."""
    if agent.get("runs_in_jobs") is False or agent.get("tools") != []:
        return []
    return [NO_TOOLS_WARNING]


def _no_shell_server_warnings(agent: dict[str, Any]) -> list[str]:
    """The warning an agent that jobs run gets when its MCP servers that run a command need a shell tool it lacks.

    Such a server is dropped at run time (``embry0.agents.mcp_servers``),
    the QA browser servers included: an agent with browser tools and no shell
    tool runs without its browser. The log tool's server is kept. A template
    step's tools replace the agent's, and saving the template warns the same
    way (``embry0.api.v1.pipeline_templates``).
    """
    from embry0.api.v1._server_warnings import no_shell_server_warning, servers_run_without

    if agent.get("runs_in_jobs") is False:
        return []
    names = servers_run_without(str(agent.get("type") or ""), agent.get("tools"), agent.get("mcp_servers"))
    return [no_shell_server_warning(names)] if names else []


def _triage_may_add_warnings(agent: dict[str, Any]) -> list[str]:
    """A warning for each skill triage may add that it never will: its source is none the agent's skills load."""
    stored = triage_may_add(agent)
    usable = set(triage_skill_choices(agent))
    return [
        f"triage_may_add_skills: triage can never add {name}: none of this agent's skills come from {skill_source(name)!r}"
        for name in stored
        if name not in usable
    ]


def _agent_warnings(agent: dict[str, Any]) -> list[str]:
    """What a write tells the operator about the row it stored: what a job will run without, and why."""
    return [*_no_tools_warnings(agent), *_no_shell_server_warnings(agent), *_triage_may_add_warnings(agent)]


async def _template_names(request: Request) -> set[str] | None:
    """Every pipeline template's name; None when no repository is wired (minimal test apps)."""
    repo = getattr(request.app.state, "templates_repo", None)
    if repo is None:
        return None
    return {str(t["name"]) for t in await repo.list_all()}


async def _reject_unrunnable_task(request: Request, agent_type: str, value: str | None) -> None:
    """A ``quest_template`` the guild could not run is a 422 naming the field (#167): one that names no
    template (the task would run the kind default instead of what the operator chose), or '' for an agent
    that does not code. Only a value the write changes is checked: a stored one whose template was deleted
    since does not stop the agent's other fields from saving."""
    problem = untemplated_task_problem(agent_type, value)
    if problem is None and value:
        problem = quest_template_problem(value, await _template_names(request))
    if problem:
        raise HTTPException(status_code=422, detail=problem)


def _with_coding(agent: dict[str, Any]) -> dict[str, Any]:
    """The row with ``coding_agent``, read-only: whether the developer node runs it, so it can take a guild
    task that names no template."""
    return {**agent, "coding_agent": str(agent.get("type") or "") in CODING_AGENT_TYPES}


@router.post("/agents", status_code=201, dependencies=[require_admin])
async def create_agent(
    req: AgentCreateRequest,
    request: Request,
    repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo),
) -> dict[str, Any]:
    await _reject_if_disabled(request, req.model)
    await _reject_unrunnable_task(request, req.type, req.quest_template)
    warnings = await check_added_skills(request, req.skills or [])
    try:
        created = await repo.create(
            agent_type=req.type,
            description=req.description,
            model=req.model,
            tools=req.tools,
            skills=req.skills,
            system_prompt=req.system_prompt,
            execution_mode=req.execution_mode,
            auth_mode=req.auth_mode,
            mcp_servers=req.mcp_servers,
            display_name=req.display_name,
            avatar=req.avatar,
            color=req.color,
            tagline=req.tagline,
            triage_may_add_skills=req.triage_may_add_skills,
            station=req.station,
            quest_template=req.quest_template,
        )
    except Exception as exc:
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail=f"Agent type '{req.type}' already exists") from exc
        raise
    forget_rows(request.app.state, "agents")
    return {**created, "warnings": [*warnings, *_agent_warnings(created)]}


def _voice_problem(voice: str) -> str | None:
    """Why ``voice`` cannot be a brief writer's voice, held to the keys both brief formats set, or None."""
    return voice_problem(
        voice, (*proposal_briefs.BRIEF_SCHEMA["properties"], *job_briefs.JOB_BRIEF_SCHEMA["properties"])
    )


#: The fields a PUT may send as null: each clears the agent's own value (#417).
_NULLABLE_ON_UPDATE: Final = frozenset({"execution_mode", "auth_mode", "quest_template"})


@router.put("/agents/{agent_type}", dependencies=[require_admin])
async def update_agent(
    agent_type: str,
    req: AgentUpdateRequest,
    request: Request,
    repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo),
) -> dict[str, Any]:
    # exclude_unset: omitted fields stay untouched, while an explicitly-sent
    # null CLEARS the column (RAV-1058 — the dashboard's "inherit default"
    # option for execution_mode/auth_mode needs a way to write NULL; the old
    # `v is not None` filter made clearing impossible over the API).
    updates = req.model_dump(exclude_unset=True)
    # Null means something for these alone: the agent inherits its mode again, or its task leaves the guild.
    # Anywhere else it has no meaning (#417): description, model and mcp_servers are NOT NULL (a 500), and a
    # NULL tools column reads as the type's default tools, a grant the tool check never sees. [] and ""
    # say "none".
    nulls = [key for key, value in updates.items() if value is None and key not in _NULLABLE_ON_UPDATE]
    if nulls:
        raise HTTPException(status_code=422, detail=f"{', '.join(sorted(nulls))} cannot be null")
    await _reject_if_disabled(request, updates.get("model"))
    if "quest_template" in updates:
        current = await repo.get(agent_type) or {}
        if updates["quest_template"] != current.get("quest_template"):
            await _reject_unrunnable_task(request, agent_type, updates["quest_template"])
    if updates.get("system_prompt"):
        # A definition no job runs is a brief writer's voice (#166): the answer's format is the code's, so a voice
        # that defines one of its keys would fight the format added after it.
        stored = await repo.get(agent_type)
        problem = (
            _voice_problem(str(updates["system_prompt"])) if stored and stored.get("runs_in_jobs") is False else None
        )
        if problem:
            raise HTTPException(status_code=422, detail=problem)
    if "tools" in updates or "mcp_servers" in updates:
        # The whole list is checked, names the row already has included: a
        # definition can only be saved with tools embry0 gates.
        stored = await repo.get(agent_type) or {}
        tools = updates["tools"] if "tools" in updates else stored.get("tools")
        servers = updates["mcp_servers"] if "mcp_servers" in updates else stored.get("mcp_servers")
        unknown = unknown_tools(tools or [], mcp_servers=servers or {})
        if unknown:
            raise HTTPException(status_code=422, detail=unknown_tools_message(agent_type, unknown))
    warnings: list[str] = []
    if updates.get("skills"):
        # Only the names this write adds are checked: the form sends the whole list
        # on every save, and names the row already has keep working (spec 3.3).
        stored = await repo.get(agent_type)
        had = set((stored or {}).get("skills") or [])
        warnings = await check_added_skills(request, [s for s in updates["skills"] if s not in had])
    if not updates:
        agent = await repo.get(agent_type)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent type not found")
        return {**agent, "warnings": [*warnings, *_agent_warnings(agent)]}
    updated = await repo.update(agent_type, **updates)
    forget_rows(request.app.state, "agents")
    return {**updated, "warnings": [*warnings, *_agent_warnings(updated)]}


@router.delete("/agents/{agent_type}", dependencies=[require_admin])
async def delete_agent(
    agent_type: str,
    request: Request,
    repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo),
) -> dict[str, Any]:
    try:
        await repo.delete(agent_type)
    except ValueError as exc:
        if "not found" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    forget_rows(request.app.state, "agents")
    return {"type": agent_type, "status": "deleted"}


@router.post("/agents/{agent_type}/reset", dependencies=[require_admin])
async def reset_agent(
    agent_type: str,
    request: Request,
    repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo),
) -> dict[str, Any]:
    try:
        reset = await repo.reset(agent_type)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    forget_rows(request.app.state, "agents")
    return reset


@router.post("/agents/{agent_type}/skills/{skill_name}", dependencies=[require_admin])
async def add_agent_skill(
    agent_type: str,
    skill_name: str,
    request: Request,
    repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo),
) -> dict[str, Any]:
    """Add one skill to an agent; adding one it already has is a no-op.

    A catalog name must be known; a bare name is the target repository's own skill.
    """
    agent = await repo.get(agent_type)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent type not found")
    skills = list(agent.get("skills") or [])
    if skill_name in skills:
        return {**agent, "warnings": []}
    warnings = await check_added_skills(request, [skill_name])
    updated = await repo.update(agent_type, skills=[*skills, skill_name])
    await audit(request, "agent.skill_added", agent_type=agent_type, skill=skill_name)
    return {**updated, "warnings": warnings}


@router.delete("/agents/{agent_type}/skills/{skill_name}", dependencies=[require_admin])
async def remove_agent_skill(
    agent_type: str,
    skill_name: str,
    request: Request,
    repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo),
) -> dict[str, Any]:
    """Remove one skill from an agent; removing one it does not have is a no-op."""
    agent = await repo.get(agent_type)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent type not found")
    skills = list(agent.get("skills") or [])
    if skill_name not in skills:
        return agent
    updated = await repo.update(agent_type, skills=[s for s in skills if s != skill_name])
    await audit(request, "agent.skill_removed", agent_type=agent_type, skill=skill_name)
    return updated
