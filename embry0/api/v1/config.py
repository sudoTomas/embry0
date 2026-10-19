"""Configuration API — budget controls and context injection settings."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from embry0.agents.model_policy import usable_default_model
from embry0.api.deps import (
    get_budget_repo,
    get_config,
    get_context_repo,
    get_integration_repo,
    get_provider_repo,
    request_actor,
    require_admin,
)
from embry0.api.schemas import (
    BudgetConfigRequest,
    BudgetConfigResponse,
    ContextConfigRequest,
    IntegrationConfigUpdate,
    ProviderConfigUpdate,
)
from embry0.audit.helpers import emit_audit
from embry0.config import Embry0Config
from embry0.services.provider_readiness import PROBE_AUDIT_ACTION, catalog_provider_for_model, readiness_by_provider
from embry0.storage.repositories.budget_config import BudgetConfigRepository
from embry0.storage.repositories.context_config import ContextConfigRepository
from embry0.storage.repositories.integration_config import _SECRET_FIELDS, IntegrationConfigRepository
from embry0.storage.repositories.provider_config import ProviderConfigRepository

router = APIRouter()


@router.get("/config/models")
async def get_model_config(
    request: Request,
    config: Embry0Config = Depends(get_config),
    provider_repo: ProviderConfigRepository = Depends(get_provider_repo),
) -> dict[str, Any]:
    """Return the current model family configuration.

    Tiers come from the DB provider_config row — the source the runtime
    actually resolves models from — with Embry0Config (env) only as the
    no-row fallback, so stale ``MODEL_*`` env values never show models the
    runtime does not use.
    The catalog is complete — Anthropic is a first-class entry, each
    provider carries its credential source, and each model an ``enabled``
    flag from provider_config.disabled_models.
    Availability and the last live test come from
    embry0.services.provider_readiness, the same answer GET /agents reports
    per agent; ``model_prefixes`` lets a client name the provider of any
    model id, a custom one included.
    Used by the frontend model selectors (AgentFormPage, JobAdvancedFields
    via useModels) and the LLM Providers page.
    """
    # RAV-1238: tiers from the DB row. Reading env-config here let stale
    # MODEL_* values show the pre-5-family models in every dashboard
    # dropdown while jobs ran on the current ones. RAV-1297: the complete
    # catalog (Anthropic was omitted as the implicit runtime default).
    from embry0.agents.providers import catalog_providers

    provider_cfg = await provider_repo.get()
    disabled = set(provider_cfg.get("disabled_models") or [])
    readiness = await readiness_by_provider(request.app.state, with_last_probe=True)
    # The default provider serves the deployment's default model, which every
    # agent runs on when one is set (#465); with none it is Anthropic, the
    # runtime's own default.
    default_serving = catalog_provider_for_model(usable_default_model(provider_cfg.get("default_model")))
    default_provider = default_serving.name if default_serving is not None else "anthropic"

    providers = []
    for p in catalog_providers():
        state = readiness[p.name]
        probe = state.last_probe
        providers.append(
            {
                "name": p.name,
                "base_url": p.base_url,
                "available": state.ready,
                "credential_source": state.credential_source,
                "default": p.name == default_provider,
                "model_prefixes": list(p.model_prefixes),
                "last_probe": (
                    {"status": probe.status, "checked_at": probe.checked_at.isoformat()} if probe is not None else None
                ),
                "models": [
                    {
                        "model": m,
                        "enabled": m not in disabled,
                        "pricing_usd_per_mtok": {
                            "input": p.pricing_usd_per_mtok.get(m, (None, None))[0],
                            "output": p.pricing_usd_per_mtok.get(m, (None, None))[1],
                        },
                    }
                    for m in p.models
                ],
            }
        )
    return {
        "heavy": provider_cfg.get("model_heavy") or config.model_heavy,
        "medium": provider_cfg.get("model_medium") or config.model_medium,
        "light": provider_cfg.get("model_light") or config.model_light,
        "providers": providers,
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
            actor=request_actor(request),
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
    req: ContextConfigRequest,
    request: Request,
    context: ContextConfigRepository = Depends(get_context_repo),
) -> dict[str, Any]:
    await context.set_global(system_context=req.system_context, assistant_context=req.assistant_context)
    db = getattr(request.app.state, "db", None)
    config = getattr(request.app.state, "config", None)
    # Char counts, not bodies — contexts are large prompt text.
    await emit_audit(
        db,
        "context_config_updated",
        actor=request_actor(request),
        details={
            "scope": "global",
            "system_context_chars": len(req.system_context or ""),
            "assistant_context_chars": len(req.assistant_context or ""),
        },
        audit_log_path=getattr(config, "audit_log_path", None),
    )
    return {"status": "updated"}


@router.get("/config/context/repos/{repo:path}")
async def get_repo_context(repo: str, context: ContextConfigRepository = Depends(get_context_repo)) -> dict[str, Any]:
    ctx = await context.get_repo(repo)
    return ctx or {"system_context": "", "assistant_context": ""}


@router.put("/config/context/repos/{repo:path}")
async def set_repo_context(
    repo: str,
    req: ContextConfigRequest,
    request: Request,
    context: ContextConfigRepository = Depends(get_context_repo),
) -> dict[str, Any]:
    await context.set_repo(repo=repo, system_context=req.system_context, assistant_context=req.assistant_context)
    db = getattr(request.app.state, "db", None)
    config = getattr(request.app.state, "config", None)
    await emit_audit(
        db,
        "context_config_updated",
        actor=request_actor(request),
        details={
            "scope": "repo",
            "repo": repo,
            "system_context_chars": len(req.system_context or ""),
            "assistant_context_chars": len(req.assistant_context or ""),
        },
        audit_log_path=getattr(config, "audit_log_path", None),
    )
    return {"status": "updated"}


# --- Integration Config ---


@router.get("/config/integrations")
async def get_integrations(
    repo: IntegrationConfigRepository = Depends(get_integration_repo),
) -> dict[str, Any]:
    return await repo.get()


@router.put("/config/integrations", dependencies=[require_admin])
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
        # Never write credential values into the audit trail — record only
        # that the field was touched. This path previously logged the webhook
        # secret and Telegram bot token verbatim; #157 adds a Linear API key
        # to the same payload, which made fixing it non-optional.
        redacted = {k: ("<set>" if v else "<cleared>") if k in _SECRET_FIELDS else v for k, v in updates.items()}
        await emit_audit(
            db,
            "integration_config_updated",
            actor=request_actor(request),
            details=redacted,
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


# RAV-1056 Phase C: admin-gated like /config/integrations — provider config
# carries credentials-adjacent settings; operator tier was an inconsistency.
@router.put("/config/provider", dependencies=[require_admin])
async def update_provider(
    req: ProviderConfigUpdate,
    request: Request,
    repo: ProviderConfigRepository = Depends(get_provider_repo),
) -> dict[str, Any]:
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if updates:
        # RAV-1297 interlock: the enablement set and the tier assignments
        # must stay mutually consistent — a disabled model in a tier slot
        # would make the tier fallback itself broken. #465: the same goes for
        # the default model, which every agent of every job resolves to.
        from embry0.agents.providers import known_models, unpriced_models, unpriced_models_message

        current = await repo.get()
        new_default = updates.get("default_model")
        if new_default and new_default not in known_models():
            raise HTTPException(
                status_code=422,
                detail=f"default_model is not a known model id: {new_default!r}",
            )
        # #219: the default and the tier models are what agents run on; one
        # that routes to a provider by prefix but has no price would run unmetered.
        unpriced = unpriced_models(
            str(updates.get(key) or "") for key in ("default_model", "model_heavy", "model_medium", "model_light")
        )
        if unpriced:
            raise HTTPException(status_code=422, detail=unpriced_models_message(unpriced))
        disabled = set(
            updates["disabled_models"] if "disabled_models" in updates else current.get("disabled_models") or []
        )
        unknown = disabled - known_models()
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"disabled_models contains unknown model ids: {sorted(unknown)}",
            )
        tiers = {tier: updates.get(tier, current.get(tier)) for tier in ("model_heavy", "model_medium", "model_light")}
        conflicts = {tier: m for tier, m in tiers.items() if m and m in disabled}
        if conflicts:
            raise HTTPException(
                status_code=422,
                detail=(
                    "a tier-assigned model cannot be disabled: "
                    + ", ".join(f"{tier}={m}" for tier, m in sorted(conflicts.items()))
                ),
            )
        default_model = updates["default_model"] if "default_model" in updates else current.get("default_model")
        if default_model and default_model in disabled:
            raise HTTPException(
                status_code=422,
                detail=f"the default model cannot be disabled: default_model={default_model}",
            )
        result = await repo.update(**updates)
        db = getattr(request.app.state, "db", None)
        config = getattr(request.app.state, "config", None)
        await emit_audit(
            db,
            "provider_config_updated",
            actor=request_actor(request),
            details=updates,
            audit_log_path=getattr(config, "audit_log_path", None),
        )
        return result
    return await repo.get()


@router.post("/config/provider/test")
async def test_provider_connection(
    request: Request,
    repo: ProviderConfigRepository = Depends(get_provider_repo),
) -> dict[str, Any]:
    result = await repo.test_connection()
    db = getattr(request.app.state, "db", None)
    config = getattr(request.app.state, "config", None)
    # Outcome only — never the config payload.
    await emit_audit(
        db,
        "provider_config_tested",
        actor=request_actor(request),
        details={"status": result.get("status")},
        audit_log_path=getattr(config, "audit_log_path", None),
    )
    return result


@router.post("/config/providers/{provider_name}/test")
async def test_catalog_provider(
    provider_name: str,
    request: Request,
) -> dict[str, Any]:
    """Live connection test for one catalog provider.

    Unlike the legacy /config/provider/test (a credential-presence report),
    this actually calls the provider's cheap authenticated models endpoint
    and decodes the failure class — unauthorized vs entitlement-refused vs
    unreachable. Credentials never appear in the result.
    """
    from embry0.agents.providers import CATALOG_PROVIDERS
    from embry0.services.provider_probe import (
        overall_status,
        probe_anthropic,
        probe_xai,
        serialize_checks,
    )

    provider = next((p for p in CATALOG_PROVIDERS if p.name == provider_name), None)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_name!r}")

    if provider_name == "anthropic":
        checks = await probe_anthropic()
    else:  # xai — the only other catalog provider today
        checks = await probe_xai(getattr(request.app.state, "xai_refresher", None))

    status = overall_status(checks)
    db = getattr(request.app.state, "db", None)
    config = getattr(request.app.state, "config", None)
    # Outcome only — never credentials or raw provider payloads.
    await emit_audit(
        db,
        PROBE_AUDIT_ACTION,
        actor=request_actor(request),
        details={"provider": provider_name, "status": status},
        audit_log_path=getattr(config, "audit_log_path", None),
    )
    return {"provider": provider_name, "status": status, "checks": serialize_checks(checks)}


# --- Context: list and delete repos ---


@router.get("/config/context/repos")
async def list_repo_contexts(
    context: ContextConfigRepository = Depends(get_context_repo),
) -> list[dict[str, Any]]:
    return await context.list_repos()


@router.delete("/config/context/repos/{repo:path}")
async def delete_repo_context(
    repo: str,
    request: Request,
    context: ContextConfigRepository = Depends(get_context_repo),
) -> dict[str, Any]:
    await context.delete_repo(repo)
    db = getattr(request.app.state, "db", None)
    config = getattr(request.app.state, "config", None)
    await emit_audit(
        db,
        "context_config_deleted",
        actor=request_actor(request),
        details={"repo": repo},
        audit_log_path=getattr(config, "audit_log_path", None),
    )
    return {"repo": repo, "status": "deleted"}
