"""Agent definitions API — CRUD for agent types."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from embry0.api.deps import get_agent_defs_repo, require_admin
from embry0.api.schemas import AgentCreateRequest, AgentUpdateRequest
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
    return annotated


@router.post("/agents", status_code=201, dependencies=[require_admin])
async def create_agent(
    req: AgentCreateRequest,
    request: Request,
    repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo),
) -> dict[str, Any]:
    await _reject_if_disabled(request, req.model)
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
    await _reject_if_disabled(request, updates.get("model"))
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
