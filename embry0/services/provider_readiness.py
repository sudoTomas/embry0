"""Provider readiness: which catalog provider serves a model, and whether it can run it now.

The runtime routes a model to a provider by id prefix (``embry0/agents/providers.py``)
and finds out whether that provider has a credential only once the agent runs: the
resolver refuses an Anthropic run without one, the executor refuses a provider run
without one, both after the job was dispatched. This module answers the same question
before dispatch, from the same sources the runtime reads: the orchestrator environment,
the xAI OAuth refresher and the credential registry. Next to that it reports the last
live connection test of each provider, read back from the audit log the test writes.

Three surfaces read it. ``GET /config/models`` reports each provider's ``available``
flag, ``credential_source`` and ``last_probe``. ``GET /agents`` reports it per agent as
``provider`` and ``provider_ready`` for the stored model, and as ``effective_provider``
and ``effective_provider_ready`` for the model a run uses (``effective_model``), which
the deployment's default model can replace. With ``AGENT_MODEL_PROVIDER_CHECK`` on,
saving an agent whose model has no ready provider is refused with a message that names
the provider.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

import structlog

from embry0.agents.model_policy import build_model_policy, resolve_tier_models
from embry0.agents.providers import ModelProvider, catalog_providers
from embry0.agents.resolver import effective_agent_model

logger = structlog.get_logger(__name__)

PROBE_AUDIT_ACTION: Final = "provider_connection_tested"
"""Audit action ``POST /config/providers/{name}/test`` writes, details ``{provider, status}``."""


class ProviderNotReadyError(ValueError):
    """An agent's model routes to a provider that cannot run it now."""


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """The outcome of a provider's most recent live connection test."""

    status: str
    checked_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderReadiness:
    name: str
    ready: bool
    credential_source: str | None
    last_probe: ProbeResult | None = None


def catalog_provider_for_model(model: str) -> ModelProvider | None:
    """The catalog provider whose id prefix matches ``model``, or None when no prefix does.

    Unlike ``provider_for_model`` this includes Anthropic, and an id that matches no
    catalog prefix is None rather than the implicit default: the runtime would hand
    such an id to the Claude CLI, which cannot run it either.
    """
    for provider in catalog_providers():
        if any(model.startswith(prefix) for prefix in provider.model_prefixes):
            return provider
    return None


async def _registry_has_provider(app_state: Any, provider: str) -> bool:
    repo = getattr(app_state, "credentials_repo", None)
    if repo is None:
        return False
    return bool(await repo.any_for_provider(provider))


async def provider_availability(app_state: Any, provider: ModelProvider) -> tuple[bool, str | None]:
    """``(available, credential_source)`` for a catalog provider.

    Anthropic authenticates via CLAUDE_CODE_OAUTH_TOKEN and/or ANTHROPIC_API_KEY; xAI via
    its console key or the SuperGrok OAuth proxy (a provisioned refresher means grok is
    runnable); an OpenAI-compatible provider via its key variable (the custom one only
    together with its base URL variable). A registry row counts only when those env
    sources are absent. A provider with none of them is not ready, and a job whose model
    routes to it fails before its sandbox exists (CREDENTIAL_MISSING).
    """
    if provider.name == "anthropic":
        oauth = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))
        api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        if oauth and api_key:
            return True, "oauth+api_key"
        if oauth:
            return True, "oauth"
        if api_key:
            return True, "api_key"
        if await _registry_has_provider(app_state, "anthropic"):
            return True, "registry"
        return False, None
    if os.environ.get(provider.api_key_env) and (not provider.base_url_env or os.environ.get(provider.base_url_env)):
        return True, "api_key"
    if provider.name == "xai" and getattr(app_state, "xai_refresher", None) is not None:
        return True, "oauth_proxy"
    if await _registry_has_provider(app_state, provider.name):
        return True, "registry"
    return False, None


async def last_probe(app_state: Any, provider_name: str) -> ProbeResult | None:
    """The newest live connection test of ``provider_name``, or None.

    None when no test was ever run, when retention pruned it, or when no audit
    repository is wired. A credential that is present but failed its last test still
    counts as available: the two are reported side by side, not merged.
    """
    repo = getattr(app_state, "audit_repo", None)
    if repo is None:
        return None
    row = await repo.latest_entry(PROBE_AUDIT_ACTION, detail_key="provider", detail_value=provider_name)
    if row is None:
        return None
    details = row.get("details") or {}
    return ProbeResult(status=str(details.get("status") or "unknown"), checked_at=row["created_at"])


async def readiness_by_provider(app_state: Any, *, with_last_probe: bool = False) -> dict[str, ProviderReadiness]:
    """Every catalog provider's readiness, keyed by provider name.

    ``with_last_probe`` adds one indexed audit-log read per provider; the agents
    list leaves it off because it reports readiness only.
    """
    out: dict[str, ProviderReadiness] = {}
    for provider in catalog_providers():
        available, source = await provider_availability(app_state, provider)
        probe = await last_probe(app_state, provider.name) if with_last_probe else None
        out[provider.name] = ProviderReadiness(provider.name, available, source, probe)
    return out


def _provider_fields(model: str, readiness: Mapping[str, ProviderReadiness]) -> tuple[str | None, bool]:
    provider = catalog_provider_for_model(model)
    state = readiness.get(provider.name) if provider is not None else None
    return (provider.name if provider is not None else None), bool(state is not None and state.ready)


def annotate_agent(
    agent: Mapping[str, Any],
    readiness: Mapping[str, ProviderReadiness],
    model_policy: Mapping[str, Any] | None = None,
    tier_models: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """``agent`` plus the provider of its stored model and of the model a run uses.

    ``provider`` (catalog provider by prefix, or None) and ``provider_ready`` are
    for the stored ``model``. ``effective_model`` is what a job that names no
    model runs, resolved under ``model_policy`` and ``tier_models`` (the config
    class defaults when None) as the executor resolves it
    (``embry0.agents.resolver.effective_agent_model``), with
    ``effective_model_source``, ``effective_provider`` and ``effective_provider_ready``.
    """
    provider, ready = _provider_fields(str(agent.get("model") or ""), readiness)
    effective = effective_agent_model(
        str(agent.get("type") or ""),
        agent,
        model_policy,
        tier_models=tier_models if tier_models is not None else resolve_tier_models(None, None),
    )
    effective_provider, effective_ready = _provider_fields(effective.model, readiness)
    return {
        **agent,
        "provider": provider,
        "provider_ready": ready,
        "effective_model": effective.model,
        "effective_model_source": effective.source,
        "effective_provider": effective_provider,
        "effective_provider_ready": effective_ready,
    }


async def deployment_provider_config(app_state: Any) -> Mapping[str, Any] | None:
    """The provider_config row (or its env defaults) agent runs read their models from.

    None without a provider repository or on a failed read: the executor then
    applies no model policy and reads each tier from ``Embry0Config``.
    """
    repo = getattr(app_state, "provider_repo", None)
    if repo is None:
        return None
    try:
        row = await repo.get()
    except Exception:
        logger.warning("agents_model_policy_load_failed", exc_info=True)
        return None
    return row if isinstance(row, Mapping) else None


async def annotate_agents(agents: list[dict[str, Any]], app_state: Any) -> list[dict[str, Any]]:
    """``annotate_agent`` for each agent under the deployment's model policy and tiers, read as runs read them."""
    readiness = await readiness_by_provider(app_state)
    row = await deployment_provider_config(app_state)
    model_policy = build_model_policy(row) if row is not None else None
    tier_models = resolve_tier_models(row, getattr(app_state, "config", None))
    return [annotate_agent(agent, readiness, model_policy, tier_models) for agent in agents]


async def ensure_model_provider_ready(model: str, app_state: Any) -> None:
    """Raise ProviderNotReadyError unless a ready catalog provider serves ``model``."""
    provider = catalog_provider_for_model(model)
    if provider is None:
        known = ", ".join(f"{p.name} ({', '.join(p.model_prefixes)})" for p in catalog_providers())
        raise ProviderNotReadyError(
            f"no configured provider serves model {model!r}; providers and their model id prefixes: {known}"
        )
    available, _ = await provider_availability(app_state, provider)
    if not available:
        raise ProviderNotReadyError(
            f"provider {provider.name!r} is not ready to run model {model!r}: no credential is configured "
            "for it. Add one on the LLM Providers page (credential registry or environment), "
            "or pick a model from a ready provider"
        )
