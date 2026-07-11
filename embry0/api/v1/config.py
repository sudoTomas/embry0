"""Configuration API — budget controls and context injection settings."""

from typing import Any

from fastapi import APIRouter, Depends, Request

from embry0.api.deps import get_budget_repo, get_config, get_context_repo, get_integration_repo, get_provider_repo
from embry0.api.schemas import (
    BudgetConfigRequest,
    BudgetConfigResponse,
    ContextConfigRequest,
    IntegrationConfigUpdate,
    ProviderConfigUpdate,
)
from embry0.audit.helpers import emit_audit
from embry0.config import Embry0Config
from embry0.storage.repositories.budget_config import BudgetConfigRepository
from embry0.storage.repositories.context_config import ContextConfigRepository
from embry0.storage.repositories.integration_config import IntegrationConfigRepository
from embry0.storage.repositories.provider_config import ProviderConfigRepository

router = APIRouter()


@router.get("/config/models")
async def get_model_config(
    config: Embry0Config = Depends(get_config),
) -> dict[str, str]:
    """Return the current model family configuration.

    Returns the three tier model IDs configured in Embry0Config.
    Used by the frontend to populate the model selector in AgentFormPage.
    """
    return {
        "heavy": config.model_heavy,
        "medium": config.model_medium,
        "light": config.model_light,
    }


@router.get("/config/budget")
async def get_budget(budget: BudgetConfigRepository = Depends(get_budget_repo)) -> BudgetConfigResponse:
    config = await budget.get()
    return BudgetConfigResponse(**{k: v for k, v in config.items() if k not in ("id", "updated_at")})


@router.put("/config/budget")
async def update_budget(
    req: BudgetConfigRequest,
    request: Request,
    budget: BudgetConfigRepository = Depends(get_budget_repo),
) -> dict[str, Any]:
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if updates:
        await budget.update(**updates)
        db = getattr(request.app.state, "db", None)
        config = getattr(request.app.state, "config", None)
        await emit_audit(
            db,
            "budget_config_updated",
            actor=request.client.host if request.client else "api",
            details=updates,
            audit_log_path=getattr(config, "audit_log_path", None),
        )
    return {"status": "updated"}


@router.get("/config/context")
async def get_global_context(context: ContextConfigRepository = Depends(get_context_repo)) -> dict[str, Any]:
    ctx = await context.get_global()
    return ctx or {"system_context": "", "assistant_context": ""}


@router.put("/config/context")
async def set_global_context(
    req: ContextConfigRequest, context: ContextConfigRepository = Depends(get_context_repo)
) -> dict[str, Any]:
    await context.set_global(system_context=req.system_context, assistant_context=req.assistant_context)
    return {"status": "updated"}


@router.get("/config/context/repos/{repo:path}")
async def get_repo_context(repo: str, context: ContextConfigRepository = Depends(get_context_repo)) -> dict[str, Any]:
    ctx = await context.get_repo(repo)
    return ctx or {"system_context": "", "assistant_context": ""}


@router.put("/config/context/repos/{repo:path}")
async def set_repo_context(
    repo: str, req: ContextConfigRequest, context: ContextConfigRepository = Depends(get_context_repo)
) -> dict[str, Any]:
    await context.set_repo(repo=repo, system_context=req.system_context, assistant_context=req.assistant_context)
    return {"status": "updated"}


# --- Integration Config ---


@router.get("/config/integrations")
async def get_integrations(
    repo: IntegrationConfigRepository = Depends(get_integration_repo),
) -> dict[str, Any]:
    return await repo.get()


@router.put("/config/integrations")
async def update_integrations(
    req: IntegrationConfigUpdate,
    request: Request,
    repo: IntegrationConfigRepository = Depends(get_integration_repo),
) -> dict[str, Any]:
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if updates:
        result = await repo.update(**updates)
        db = getattr(request.app.state, "db", None)
        config = getattr(request.app.state, "config", None)
        await emit_audit(
            db,
            "integration_config_updated",
            actor=request.client.host if request.client else "api",
            details=updates,
            audit_log_path=getattr(config, "audit_log_path", None),
        )
        return result
    return await repo.get()


# --- Provider Config ---


@router.get("/config/provider")
async def get_provider(
    repo: ProviderConfigRepository = Depends(get_provider_repo),
) -> dict[str, Any]:
    return await repo.get()


@router.put("/config/provider")
async def update_provider(
    req: ProviderConfigUpdate,
    request: Request,
    repo: ProviderConfigRepository = Depends(get_provider_repo),
) -> dict[str, Any]:
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if updates:
        result = await repo.update(**updates)
        db = getattr(request.app.state, "db", None)
        config = getattr(request.app.state, "config", None)
        await emit_audit(
            db,
            "provider_config_updated",
            actor=request.client.host if request.client else "api",
            details=updates,
            audit_log_path=getattr(config, "audit_log_path", None),
        )
        return result
    return await repo.get()


@router.post("/config/provider/test")
async def test_provider_connection(
    repo: ProviderConfigRepository = Depends(get_provider_repo),
) -> dict[str, Any]:
    return await repo.test_connection()


# --- Context: list and delete repos ---


@router.get("/config/context/repos")
async def list_repo_contexts(
    context: ContextConfigRepository = Depends(get_context_repo),
) -> list[dict[str, Any]]:
    return await context.list_repos()


@router.delete("/config/context/repos/{repo:path}")
async def delete_repo_context(
    repo: str,
    context: ContextConfigRepository = Depends(get_context_repo),
) -> dict[str, Any]:
    await context.delete_repo(repo)
    return {"repo": repo, "status": "deleted"}
