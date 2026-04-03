"""Configuration API — budget controls and context injection settings."""
from fastapi import APIRouter, Depends

from legion.api.deps import get_budget_repo, get_context_repo
from legion.api.schemas import BudgetConfigRequest, BudgetConfigResponse, ContextConfigRequest
from legion.storage.repositories.budget_config import BudgetConfigRepository
from legion.storage.repositories.context_config import ContextConfigRepository

router = APIRouter()


@router.get("/config/budget")
async def get_budget(budget: BudgetConfigRepository = Depends(get_budget_repo)) -> BudgetConfigResponse:
    config = await budget.get()
    return BudgetConfigResponse(**{k: v for k, v in config.items() if k not in ("id", "updated_at")})


@router.put("/config/budget")
async def update_budget(req: BudgetConfigRequest, budget: BudgetConfigRepository = Depends(get_budget_repo)) -> dict:
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if updates:
        await budget.update(**updates)
    return {"status": "updated"}


@router.get("/config/context")
async def get_global_context(context: ContextConfigRepository = Depends(get_context_repo)) -> dict:
    ctx = await context.get_global()
    return ctx or {"system_context": "", "assistant_context": ""}


@router.put("/config/context")
async def set_global_context(
    req: ContextConfigRequest, context: ContextConfigRepository = Depends(get_context_repo)
) -> dict:
    await context.set_global(system_context=req.system_context, assistant_context=req.assistant_context)
    return {"status": "updated"}


@router.get("/config/context/repos/{repo:path}")
async def get_repo_context(repo: str, context: ContextConfigRepository = Depends(get_context_repo)) -> dict:
    ctx = await context.get_repo(repo)
    return ctx or {"system_context": "", "assistant_context": ""}


@router.put("/config/context/repos/{repo:path}")
async def set_repo_context(
    repo: str, req: ContextConfigRequest, context: ContextConfigRepository = Depends(get_context_repo)
) -> dict:
    await context.set_repo(repo=repo, system_context=req.system_context, assistant_context=req.assistant_context)
    return {"status": "updated"}
