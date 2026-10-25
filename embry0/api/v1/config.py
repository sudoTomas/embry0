"""Configuration API — budget controls and context injection settings."""

import os
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
    from embry0.agents.openrouter_catalog import catalog_model
    from embry0.agents.providers import OPENROUTER, catalog_providers, model_rates, provider_models

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
                # The custom provider's root is the operator's; the env one is shown when set.
                "base_url": p.base_url or (os.environ.get(p.base_url_env, "") if p.base_url_env else ""),
                # docs/providers.md: the OpenAI-compatible family, xAI included.
                "family": "openai_compatible"
                if (p.openai_base_url or p.runtime == "chat_completions")
                else "anthropic",
                "available": state.ready,
                "credential_source": state.credential_source,
                "default": p.name == default_provider,
                "model_prefixes": list(p.model_prefixes),
                "last_probe": (
                    {"status": probe.status, "checked_at": probe.checked_at.isoformat()} if probe is not None else None
                ),
                "models": [
                    _catalog_entry(p, m, m not in disabled, model_rates(m), catalog_model(m))
                    for m in provider_models(p)
                ],
            }
        )
        if p.name == OPENROUTER.name:
            # Where OpenRouter's list comes from: its live catalog, or the static entry until one is read.
            service = getattr(request.app.state, "openrouter_catalog", None)
            providers[-1]["catalog"] = service.status() if service is not None else {"source": "static"}
    return {
        "heavy": provider_cfg.get("model_heavy") or config.model_heavy,
        "medium": provider_cfg.get("model_medium") or config.model_medium,
        "light": provider_cfg.get("model_light") or config.model_light,
        "providers": providers,
    }


def _catalog_entry(provider: Any, model: str, enabled: bool, rates: Any, live: Any) -> dict[str, Any]:
    """One model of GET /config/models: its enablement, tool calling and price, and for an OpenRouter
    catalog model its name, maker and context window."""
    entry: dict[str, Any] = {
        "model": model,
        "enabled": enabled,
        # False: served without tool calling on this provider's API (chat-only).
        "tool_calling": model not in provider.chat_only_models,
        "pricing_usd_per_mtok": {
            "input": rates[0] if rates is not None else None,
            "output": rates[1] if rates is not None else None,
        },
    }
    if live is not None:
        entry["name"] = live.name
        entry["vendor"] = live.vendor
        entry["context_length"] = live.context_length
    return entry


@router.post("/config/providers/openrouter/catalog/refresh", dependencies=[require_admin])
async def refresh_openrouter_catalog(request: Request) -> dict[str, Any]:
    """Read OpenRouter's model list now (it is read daily by itself). Returns the list's state.

    A failed read keeps the list in force and comes back in ``last_error``.
    """
    service = getattr(request.app.state, "openrouter_catalog", None)
    if service is None:
        raise HTTPException(status_code=503, detail="OpenRouter's model list is not available on this deployment")
    status = await service.refresh()
    db = getattr(request.app.state, "db", None)
    config = getattr(request.app.state, "config", None)
    await emit_audit(
        db,
        "provider_catalog_refreshed",
        actor=request_actor(request),
        details={"provider": "openrouter", "models": status.get("models"), "error": status.get("last_error")},
        audit_log_path=getattr(config, "audit_log_path", None),
    )
    return dict(status)


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


def _check_fallback_order(order: list[str], disabled: set[str], *, changed: bool) -> None:
    """422 for a provider fallback order an agent could not run on (spec 2026-10-25).

    ``changed`` says the request sets the order: its ids are checked. The
    disabled interlock is checked either way, so a model the order names can
    no more be disabled than a tier model can.
    """
    from embry0.agents.providers import (
        chat_only_models,
        is_custom_model,
        known_models,
        unpriced_models,
        unpriced_models_message,
    )

    if changed:
        repeated = sorted({m for m in order if order.count(m) > 1})
        if repeated:
            raise HTTPException(status_code=422, detail=f"fallback_models names a model more than once: {repeated}")
        # A custom/<id> is the operator's own server (local models): no catalogue lists it.
        unknown = [m for m in order if m not in known_models() and not is_custom_model(m)]
        if unknown:
            raise HTTPException(status_code=422, detail=f"fallback_models contains unknown model ids: {unknown}")
        unpriced = unpriced_models(order)
        if unpriced:
            raise HTTPException(status_code=422, detail=unpriced_models_message(unpriced))
        chat_only = chat_only_models(order)
        if chat_only:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"chat-only model(s) cannot be a fallback model: {', '.join(chat_only)} — a fallback replaces "
                    "the model of agents that use tools (docs/providers.md)"
                ),
            )
    off = [m for m in order if m in disabled]
    if off:
        raise HTTPException(
            status_code=422,
            detail=(f"a fallback model cannot be disabled: {', '.join(off)} — remove it from the fallback order first"),
        )


def _check_watcher_local_model(updates: dict[str, Any]) -> None:
    """422 for a watcher local-first model that is not one of the operator's own server (local models).

    "" turns local-first off. Anything else must be a ``custom/<id>`` the
    enabled custom provider serves: local-first is for the local server, and
    a cloud model there would quietly move the watcher's spend.
    """
    from embry0.agents.providers import is_custom_model

    model = updates.get("watcher_local_model")
    if model is None:
        return
    model = str(model).strip()
    updates["watcher_local_model"] = model
    if model and not is_custom_model(model):
        raise HTTPException(
            status_code=422,
            detail=(
                f"watcher_local_model must be a custom/<model> of the local model server, with the custom "
                f"provider enabled (OPENAI_COMPATIBLE_PROVIDERS), or empty: {model!r} (docs/providers.md)"
            ),
        )


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
        from embry0.agents.providers import chat_only_models, known_models, unpriced_models, unpriced_models_message

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
        # Every agent with tools resolves to the default model or a tier one; a
        # chat-only model there would leave each of them refused at run time.
        chat_only = chat_only_models(
            str(updates.get(key) or "") for key in ("default_model", "model_heavy", "model_medium", "model_light")
        )
        if chat_only:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"chat-only model(s) cannot be the default or a tier model: {', '.join(chat_only)} — their "
                    "provider serves them without tool calling, and the agents these settings reach use tools "
                    "(docs/providers.md)"
                ),
            )
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
        _check_fallback_order(
            updates["fallback_models"] if "fallback_models" in updates else current.get("fallback_models") or [],
            disabled,
            changed="fallback_models" in updates,
        )
        _check_watcher_local_model(updates)
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
    from embry0.agents.providers import catalog_providers
    from embry0.services.provider_probe import (
        overall_status,
        probe_anthropic,
        probe_chat_completions_provider,
        probe_xai,
        serialize_checks,
    )

    provider = next((p for p in catalog_providers() if p.name == provider_name), None)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_name!r}")

    if provider_name == "anthropic":
        checks = await probe_anthropic()
    elif provider_name == "xai":
        checks = await probe_xai(getattr(request.app.state, "xai_refresher", None))
    else:  # an enabled OpenAI-compatible provider
        checks = await probe_chat_completions_provider(
            provider, getattr(request.app.state, "credential_resolver", None)
        )

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


@router.get("/config/providers/{provider_name}/usage")
async def catalog_provider_usage(provider_name: str, request: Request, fresh: bool = False) -> dict[str, Any]:
    """What the provider says its key has spent: today, this week, this month, its limit, the account's credit.

    Read with the key a job with no repository binding would run with (the
    one the connection test probes) and kept five minutes; ``fresh`` asks
    again now. Only OpenRouter answers this with an ordinary key; another
    provider is 404. Never returns the key.
    """
    from embry0.agents.providers import catalog_providers
    from embry0.services.provider_usage import SUPPORTED, ProviderUsage

    provider = next((p for p in catalog_providers() if p.name == provider_name), None)
    if provider is None or provider_name not in SUPPORTED:
        raise HTTPException(status_code=404, detail=f"no spend to read for provider {provider_name!r}")
    usage = getattr(request.app.state, "provider_usage", None)
    if usage is None:
        usage = request.app.state.provider_usage = ProviderUsage(_job_key_resolver(request))
    return await usage.read(provider_name, fresh=fresh)


def _job_key_resolver(request: Request) -> Any:
    """The key a job with no repository binding gets for a provider: the global pool, then the env; None without one."""
    return job_key_resolver(request.app.state)


def job_key_resolver(state: Any) -> Any:
    """``_job_key_resolver`` over the app's state, for a caller with no request (OpenRouter's model list)."""
    from embry0.agents.providers import provider_named
    from embry0.services.credentials.resolver import CredentialMissingError, CredentialUnavailableError

    async def resolve(name: str) -> str | None:
        resolver = getattr(state, "credential_resolver", None)
        if resolver is not None:
            try:
                return str((await resolver.resolve_llm("", name)).secret)
            except (CredentialMissingError, CredentialUnavailableError):
                return None
        provider = provider_named(name)
        if provider is None:
            return None
        return os.environ.get(provider.api_key_env, "").strip() or None

    return resolve


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
