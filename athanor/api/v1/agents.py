"""Agent definitions API — CRUD for agent types."""

from fastapi import APIRouter, Depends, HTTPException

from athanor.api.deps import get_agent_defs_repo
from athanor.api.schemas import AgentCreateRequest, AgentUpdateRequest
from athanor.storage.repositories.agent_definitions import AgentDefinitionsRepository

router = APIRouter()


@router.get("/agents")
async def list_agents(repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo)) -> list[dict]:
    return await repo.list_all()


@router.get("/agents/{agent_type}")
async def get_agent(agent_type: str, repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo)) -> dict:
    agent = await repo.get(agent_type)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent type not found")
    return agent


@router.post("/agents", status_code=201)
async def create_agent(
    req: AgentCreateRequest,
    repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo),
) -> dict:
    try:
        return await repo.create(
            agent_type=req.type,
            description=req.description,
            model=req.model,
            tools=req.tools,
            skills=req.skills,
            system_prompt=req.system_prompt,
        )
    except Exception as exc:
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail=f"Agent type '{req.type}' already exists") from exc
        raise


@router.put("/agents/{agent_type}")
async def update_agent(
    agent_type: str,
    req: AgentUpdateRequest,
    repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo),
) -> dict:
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        agent = await repo.get(agent_type)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent type not found")
        return agent
    return await repo.update(agent_type, **updates)


@router.delete("/agents/{agent_type}")
async def delete_agent(
    agent_type: str,
    repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo),
) -> dict:
    try:
        await repo.delete(agent_type)
    except ValueError as exc:
        if "not found" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"type": agent_type, "status": "deleted"}


@router.post("/agents/{agent_type}/reset")
async def reset_agent(
    agent_type: str,
    repo: AgentDefinitionsRepository = Depends(get_agent_defs_repo),
) -> dict:
    try:
        return await repo.reset(agent_type)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
