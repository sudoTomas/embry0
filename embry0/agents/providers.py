"""Model→provider routing for the Agent SDK runtime (EMB-36).

embry0's ``anthropic_api``/``oauth`` modes both run the Claude Code CLI,
which honors ``ANTHROPIC_BASE_URL``/``ANTHROPIC_API_KEY`` — so an
Anthropic-SDK-compatible backend (xAI grok via ``https://api.x.ai``) is
provider *configuration*, not a new runtime: MCP servers, safety hooks,
session resume, and token/trace plumbing all survive unchanged.

Selection is per-agent by model id through the existing ``agent_models``
plumbing: when a resolved model matches a non-Anthropic provider's
prefix, the executor overlays the provider env on the CLI subprocess
(``ClaudeAgentOptions.env``) for that agent only. The provider API key
rides the sandbox container env (injected at create from the
orchestrator's environment, reserved from user override) — it is never
serialized through the invocation argv.

The OpenAI-compatible family (docs/providers.md): every provider whose API
speaks OpenAI chat completions carries ``openai_base_url``. xAI is a member:
its readiness probe is the family's ``GET /models``, while its agents keep
the runtime above. The other members (``OPENAI_COMPATIBLE``) run on
``OpenAICompatibleExecutor`` and are off until the deployment names them in
``OPENAI_COMPATIBLE_PROVIDERS``: while off, no model routes to them and no
catalog surface lists them, so a deployment that sets nothing behaves as
before.

The providers, their models and prices are the provider catalogue
(``embry0.agents.catalogue``, ADR 0031): a schema in code, the seed embry0
ships in ``providers.yaml``. Every function here answers from the catalogue
installed in this process, the seed until a deployment's own is installed.

Caveats (documented on EMB-36):
- ``cache_control``/prompt-cache behavior on the compat layer is xAI's,
  not Anthropic's — treat cache_read/cache_creation trace columns as
  best-effort for grok runs (usage parsing is tolerant: absent fields
  become 0).
- grok-4.5 is unavailable to EU API consoles.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Final

import structlog

from embry0.agents.catalogue import ModelProvider, current, seed
from embry0.agents.openrouter_catalog import catalog_model_ids, catalog_rates

logger = structlog.get_logger(__name__)

__all__ = ["ModelProvider"]

# The providers embry0 ships, as the seed catalogue (embry0/agents/providers.yaml) builds them. What this
# process runs on is the installed catalogue (``embry0.agents.catalogue.current``), which every function below
# reads; until a deployment's own catalogue is installed it is the seed, and these are its providers.
_SHIPPED = seed()


def _shipped(name: str) -> ModelProvider:
    found = _SHIPPED.named(name)
    assert found is not None, f"the shipped catalogue has no {name!r} provider"
    return found


XAI = _shipped("xai")
ANTHROPIC = _shipped("anthropic")
OPENAI = _shipped("openai")
OPENROUTER = _shipped("openrouter")
GOOGLE = _shipped("google")
GROQ = _shipped("groq")
DEEPSEEK = _shipped("deepseek")
CUSTOM = _shipped("custom")

PROVIDERS: tuple[ModelProvider, ...] = _SHIPPED.overlay_providers
"""Non-Anthropic env-overlay registry — never add ANTHROPIC here."""

CATALOG_PROVIDERS: tuple[ModelProvider, ...] = (ANTHROPIC, *PROVIDERS)
"""The providers every deployment has, for catalog surfaces (GET /config/models, providers page).
``catalog_providers()`` adds the enabled OpenAI-compatible ones."""

OPENAI_COMPATIBLE: tuple[ModelProvider, ...] = _SHIPPED.chat_completions
"""The providers that run on OpenAICompatibleExecutor, each off until enabled."""

ALL_PROVIDERS: tuple[ModelProvider, ...] = (*CATALOG_PROVIDERS, *OPENAI_COMPATIBLE)

ENABLE_SETTING: Final = "OPENAI_COMPATIBLE_PROVIDERS"
"""Orchestrator-env setting: comma-separated names from ``OPENAI_COMPATIBLE``. Empty (the
default) enables none of them."""

_warned_settings: set[str] = set()


def enabled_openai_compatible(environ: Mapping[str, str] | None = None) -> tuple[ModelProvider, ...]:
    """The installed catalogue's OpenAI-compatible providers that are on, in catalog order: those it enables,
    and those ``OPENAI_COMPATIBLE_PROVIDERS`` names."""
    catalogue = current()
    family = catalogue.chat_completions
    raw = (os.environ if environ is None else environ).get(ENABLE_SETTING, "")
    names = {part.strip().lower() for part in raw.split(",") if part.strip()}
    known = {p.name for p in family}
    unknown = sorted(names - known)
    if unknown and raw not in _warned_settings:
        _warned_settings.add(raw)
        logger.warning(
            "openai_compatible_providers_unknown", unknown=unknown, known=sorted(known), setting=ENABLE_SETTING
        )
    return tuple(p for p in family if p.name in names or catalogue.enabled(p.name))


def routed_providers(environ: Mapping[str, str] | None = None) -> tuple[ModelProvider, ...]:
    """The non-Anthropic providers a model id can route to in this deployment."""
    return (*current().overlay_providers, *enabled_openai_compatible(environ))


def catalog_providers(environ: Mapping[str, str] | None = None) -> tuple[ModelProvider, ...]:
    """Every provider this deployment offers, Anthropic first."""
    catalogue = current()
    return (catalogue.anthropic, *catalogue.overlay_providers, *enabled_openai_compatible(environ))


def provider_named(name: str | None) -> ModelProvider | None:
    """The provider called ``name``, enabled or not. The sandbox looks providers up by the
    name the orchestrator routed to, so it needs no copy of the setting."""
    return current().named(name)


def provider_for_model(model: str) -> ModelProvider | None:
    """The non-Anthropic provider serving ``model``, or None for Anthropic."""
    for provider in routed_providers():
        if any(model.startswith(prefix) for prefix in provider.model_prefixes):
            return provider
    return None


def upstream_model(provider: ModelProvider, model: str) -> str:
    """The id the provider's API knows ``model`` by."""
    prefix = provider.model_prefixes[0] if provider.model_prefixes else ""
    if provider.strip_routing_prefix and prefix and model.startswith(prefix):
        return model[len(prefix) :]
    return model


def provider_models(provider: ModelProvider) -> tuple[str, ...]:
    """The model ids ``provider`` offers: OpenRouter's live catalog once one is loaded, else the static list.

    The catalog replaces OpenRouter's static entry rather than adding to it: a
    model OpenRouter stopped listing no longer runs there, and offering it
    would only produce failing jobs.
    """
    if provider.name == OPENROUTER.name:
        live = catalog_model_ids()
        if live is not None:
            return live
    return provider.models


def model_rates(model: str) -> tuple[float, float] | None:
    """``model``'s (input, output) $/Mtok now: OpenRouter's live price, its own entry in the installed catalogue
    (its price in force today), else its provider's default rates."""
    live = catalog_rates(model)
    if live is not None:
        return live
    catalogue = current()
    rates = catalogue.rates(model)
    return rates if rates is not None else catalogue.default_rates(model)


CACHE_READ_FACTOR = 0.1
"""Cached input is billed at a tenth of the input rate (Anthropic; applied to every provider as an estimate)."""
CACHE_WRITE_FACTOR = 1.25
"""Cache creation is billed at 125% of the input rate (same caveat)."""


def price_usage(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    rates: tuple[float, float] | None = None,
) -> float | None:
    """Estimated USD for one message's usage, or None when the model is unpriced.

    ``rates`` are the run's own (input, output) $/Mtok when the orchestrator
    sent them: a model of OpenRouter's live catalog, which the sandbox has no
    copy of.

    turn_usage events carry this so cost can tick during a run; cost_update
    (the provider's own total at the end of the run) stays authoritative.
    Prices every known provider, enabled or not: the sandbox prices the model
    the orchestrator routed it, without the orchestrator's settings.
    """
    rates = rates if rates is not None else model_rates(model)
    if rates is None:
        return None
    rate_in, rate_out = rates
    per_mtok = (
        input_tokens * rate_in
        + output_tokens * rate_out
        + cache_read_tokens * rate_in * CACHE_READ_FACTOR
        + cache_creation_tokens * rate_in * CACHE_WRITE_FACTOR
    )
    return per_mtok / 1_000_000


def known_models() -> set[str]:
    """Every model id any catalog provider of this deployment declares."""
    return {m for provider in catalog_providers() for m in provider_models(provider)}


def unpriced_models(models: Iterable[str]) -> list[str]:
    """The ids in ``models`` that route to a non-Anthropic provider with no price entry.

    Such a provider reports no cost of its own, so its token counts priced from
    ``pricing_usd_per_mtok`` are the only accounting there is (#219). A model that
    routes there by prefix but has no rates would run unmetered, so it is refused
    wherever a model id is configured, and again when an agent's model resolves.
    Anthropic models are not checked: the Claude CLI reports their cost.
    """
    found: set[str] = set()
    for model in models:
        if not model:
            continue
        if provider_for_model(model) is not None and model_rates(model) is None:
            found.add(model)
    return sorted(found)


def unpriced_models_message(models: Iterable[str]) -> str:
    """The operator-facing refusal for ``unpriced_models``' result."""
    return (
        f"model(s) have no price entry: {', '.join(models)} — a provider-routed model "
        "must be priced in the provider catalogue (embry0/agents/providers.yaml) or, for "
        "OpenRouter, in its live model list, or its runs would be metered at $0"
    )


def chat_only_models(models: Iterable[str]) -> list[str]:
    """The ids in ``models`` their routed provider serves without tool calling."""
    found: set[str] = set()
    for model in models:
        provider = provider_for_model(model) if model else None
        if provider is not None and model in provider.chat_only_models:
            found.add(model)
    return sorted(found)


def is_custom_model(model: str) -> bool:
    """Whether ``model`` is a ``custom/<id>`` the enabled custom provider serves.

    The operator's own server lists no catalogue: any id it serves routes by
    the prefix and is priced at $0. Local models (docs/providers.md) let such
    an id be the watcher's local-first model and a fallback model.
    """
    provider = provider_for_model(model) if model else None
    return provider is not None and provider.name == CUSTOM.name and len(model) > len(provider.model_prefixes[0])


def chat_only_message(provider: ModelProvider, model: str, agent_type: str, tools: Iterable[str]) -> str:
    """The refusal for an agent that needs tools on a model served without tool calling."""
    return (
        f"model {model!r} is chat-only on {provider.name}'s OpenAI-compatible API (no tool calling there), "
        f"and agent {agent_type!r} needs tools ({', '.join(tools)}); pick a model with tool calling "
        "(docs/providers.md)"
    )
