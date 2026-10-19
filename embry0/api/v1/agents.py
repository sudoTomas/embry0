"""Agent definitions API — CRUD for agent types."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from embry0.agents.skills import UnknownSkillError, check_skill_names
from embry0.api.deps import get_agent_defs_repo, require_admin
from embry0.api.schemas import AgentCreateRequest, AgentUpdateRequest
from embry0.api.v1._audit import audit
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
from embry0.storage.repositories.agent_definitions import AgentDefinitionsRepository

router = APIRouter()


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


async def check_catalog_skills(request: Request, names: list[str] | None) -> None:
    """A save naming a skill the catalog does not know is a 422 naming the closest known ones."""
    if not names:
        return
    known = await catalog_skill_names(request)
    if known is None:
        return
    try:
        check_skill_names(names, known)
    except UnknownSkillError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/agents")
async def list_agents(
    request: Request,
    repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo),
) -> list[dict[str, Any]]:
    """Every agent definition, each with the provider its model routes to and
    whether that provider has a credential right now (``provider``, ``provider_ready``)."""
    return await annotate_agents(await repo.list_all(), request.app.state)


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
    known = await catalog_skill_names(request)
    annotated["orphan_skills"] = (
        [s for s in annotated.get("skills") or [] if s not in known] if known is not None else []
    )
    return annotated


@router.post("/agents", status_code=201, dependencies=[require_admin])
async def create_agent(
    req: AgentCreateRequest,
    request: Request,
    repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo),
) -> dict[str, Any]:
    await _reject_if_disabled(request, req.model)
    await check_catalog_skills(request, req.skills)
    try:
        return await repo.create(
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
        )
    except Exception as exc:
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail=f"Agent type '{req.type}' already exists") from exc
        raise


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
    # The identity columns are NOT NULL: unlike execution_mode, null has no meaning there.
    for key in ("display_name", "avatar", "color", "tagline"):
        if key in updates and updates[key] is None:
            raise HTTPException(status_code=422, detail=f"{key} cannot be null")
    await _reject_if_disabled(request, updates.get("model"))
    await check_catalog_skills(request, updates.get("skills"))
    if not updates:
        agent = await repo.get(agent_type)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent type not found")
        return agent
    return await repo.update(agent_type, **updates)


@router.delete("/agents/{agent_type}", dependencies=[require_admin])
async def delete_agent(
    agent_type: str,
    repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo),
) -> dict[str, Any]:
    try:
        await repo.delete(agent_type)
    except ValueError as exc:
        if "not found" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"type": agent_type, "status": "deleted"}


@router.post("/agents/{agent_type}/reset", dependencies=[require_admin])
async def reset_agent(
    agent_type: str,
    repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo),
) -> dict[str, Any]:
    try:
        return await repo.reset(agent_type)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/agents/{agent_type}/skills/{skill_name}", dependencies=[require_admin])
async def add_agent_skill(
    agent_type: str,
    skill_name: str,
    request: Request,
    repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo),
) -> dict[str, Any]:
    """Add one catalog skill to an agent; adding one it already has is a no-op."""
    agent = await repo.get(agent_type)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent type not found")
    await check_catalog_skills(request, [skill_name])
    skills = list(agent.get("skills") or [])
    if skill_name in skills:
        return agent
    updated = await repo.update(agent_type, skills=[*skills, skill_name])
    await audit(request, "agent.skill_added", agent_type=agent_type, skill=skill_name)
    return updated


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
