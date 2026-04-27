"""Configuration API — budget controls and context injection settings."""

from fastapi import APIRouter, Depends

from athanor.api.deps import get_budget_repo, get_context_repo, get_integration_repo, get_provider_repo
from athanor.api.schemas import (
    BudgetConfigRequest,
    BudgetConfigResponse,
    ContextConfigRequest,
    IntegrationConfigUpdate,
    ProviderConfigUpdate,
)
from athanor.storage.repositories.budget_config import BudgetConfigRepository
from athanor.storage.repositories.context_config import ContextConfigRepository
from athanor.storage.repositories.integration_config import IntegrationConfigRepository
from athanor.storage.repositories.provider_config import ProviderConfigRepository

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


# --- Integration Config ---


@router.get("/config/integrations")
async def get_integrations(
    repo: IntegrationConfigRepository = Depends(get_integration_repo),
) -> dict:
    return await repo.get()


@router.put("/config/integrations")
async def update_integrations(
    req: IntegrationConfigUpdate,
    repo: IntegrationConfigRepository = Depends(get_integration_repo),
) -> dict:
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if updates:
        return await repo.update(**updates)
    return await repo.get()


# --- Provider Config ---


@router.get("/config/provider")
async def get_provider(
    repo: ProviderConfigRepository = Depends(get_provider_repo),
) -> dict:
    return await repo.get()


@router.put("/config/provider")
async def update_provider(
    req: ProviderConfigUpdate,
    repo: ProviderConfigRepository = Depends(get_provider_repo),
) -> dict:
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if updates:
        return await repo.update(**updates)
    return await repo.get()


@router.post("/config/provider/test")
async def test_provider_connection(
    repo: ProviderConfigRepository = Depends(get_provider_repo),
) -> dict:
    return await repo.test_connection()


# --- Context: list and delete repos ---


@router.get("/config/context/repos")
async def list_repo_contexts(
    context: ContextConfigRepository = Depends(get_context_repo),
) -> list[dict]:
    return await context.list_repos()


@router.delete("/config/context/repos/{repo:path}")
async def delete_repo_context(
    repo: str,
    context: ContextConfigRepository = Depends(get_context_repo),
) -> dict:
    await context.delete_repo(repo)
    return {"repo": repo, "status": "deleted"}
