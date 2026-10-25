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
from dataclasses import dataclass, field
from typing import Any, Final

import structlog

from embry0.agents.openrouter_catalog import catalog_model_ids, catalog_rates

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ModelProvider:
    name: str
    base_url: str
    api_key_env: str
    """Orchestrator-env var holding this provider's API key: the resolver's last step.
    For xAI it is also the sandbox-env var the CLI overlay reads."""
    model_prefixes: tuple[str, ...]
    models: tuple[str, ...]
    """Known model ids, surfaced by GET /config/models."""
    pricing_usd_per_mtok: dict[str, tuple[float, float]]
    """model -> (input $/Mtok, output $/Mtok) — catalog metadata only."""
    turn_budget_factor: float = 1.0
    """Multiplier on an agent's ``max_turns`` when this provider serves it."""
    timeout_factor: float = 1.0
    """Multiplier on an agent's ``timeout_seconds`` when this provider serves it."""
    openai_base_url: str = ""
    """Root of the provider's OpenAI-compatible API: ``/chat/completions`` and the
    probe path hang off it. Empty: not in the family, or (with ``base_url_env``)
    given by the operator."""
    runtime: str = "sdk"
    """``sdk``: the Claude Agent SDK, with the env overlay for a non-Anthropic provider.
    ``chat_completions``: ``OpenAICompatibleExecutor`` over the OpenAI-compatible API."""
    sandbox_key_env: str = ""
    """Sandbox-env var the chat-completions executor reads the key from. In the
    reserved ``EMBRY0_LLM_`` family, so a repository's own ``OPENAI_API_KEY`` (and the
    like) is never refused or overwritten."""
    base_url_env: str = ""
    """Orchestrator-env var with an operator-given base URL (the env fallback of a
    provider with no fixed endpoint). A registry credential carries it as metadata."""
    sandbox_base_url_env: str = ""
    """Sandbox-env var the executor reads an operator-given base URL from."""
    strip_routing_prefix: bool = False
    """Model ids carry ``model_prefixes[0]`` only to route; the upstream id is the rest."""
    chat_only_models: frozenset[str] = frozenset()
    """Models this provider serves without tool calling on its OpenAI-compatible API."""
    request_extras: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    """model -> extra chat-completions body fields a model needs (e.g. to enable tools)."""
    default_pricing_usd_per_mtok: tuple[float, float] | None = None
    """Rates for a routed model with no entry of its own; None refuses such a model (#219)."""
    probe_path: str = "/models"
    """Readiness probe: ``GET <openai_base_url><probe_path>`` with the key."""


XAI = ModelProvider(
    name="xai",
    base_url="https://api.x.ai",
    api_key_env="XAI_API_KEY",
    model_prefixes=("grok-",),
    models=("grok-4.5", "grok-4.7"),
    # Flat per-token rates. xAI doubles both above 200K prompt tokens and
    # bills cached input lower; neither is modelled (docs/providers.md).
    pricing_usd_per_mtok={"grok-4.5": (2.0, 6.0), "grok-4.7": (2.0, 6.0)},
    # The turn and time budgets are sized for Claude. grok-4.7 makes ~3x the tool calls of a
    # Claude developer run (median 111 vs 37 over 30 days) and takes ~2x as long at p90; on
    # Claude-sized budgets 4 of its first 8 developer runs died at the turn cap.
    turn_budget_factor=2.0,
    timeout_factor=2.0,
    # A member of the OpenAI-compatible family for its readiness probe only: its agents
    # keep the Agent SDK (or DirectXaiExecutor) over the Anthropic-compatible endpoint.
    openai_base_url="https://api.x.ai/v1",
)

# RAV-1297: Anthropic as a first-class catalog entry. It stays OUT of
# ``PROVIDERS`` — that tuple is the env-overlay registry (executor overlays
# provider auth, sandbox_manager injects api_key_env, resolver skips the
# Anthropic credential check when a model matches), and Anthropic is the
# implicit default all of that machinery defines itself against.
# Sonnet 5 sticker pricing ($2/$10 intro runs through 2026-08-31); pricing
# here is catalog metadata, not billing truth.
ANTHROPIC = ModelProvider(
    name="anthropic",
    base_url="https://api.anthropic.com",
    api_key_env="ANTHROPIC_API_KEY",
    model_prefixes=("claude-",),
    models=("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"),
    pricing_usd_per_mtok={
        "claude-opus-5": (5.0, 25.0),
        "claude-sonnet-5": (3.0, 15.0),
        "claude-haiku-4-5": (1.0, 5.0),
    },
)

# The OpenAI-compatible providers that run on OpenAICompatibleExecutor. Prices are the
# providers' published list prices as of 2026-10-18 (docs/providers.md names the pages);
# like every entry here they are catalog metadata, not billing truth.
OPENAI = ModelProvider(
    name="openai",
    base_url="https://api.openai.com/v1",
    api_key_env="OPENAI_API_KEY",
    model_prefixes=("gpt-",),
    models=("gpt-6.1-sol", "gpt-6-luna"),
    pricing_usd_per_mtok={"gpt-6.1-sol": (2.0, 10.0), "gpt-6-luna": (0.10, 0.50)},
    openai_base_url="https://api.openai.com/v1",
    runtime="chat_completions",
    sandbox_key_env="EMBRY0_LLM_OPENAI_API_KEY",
    # OpenAI serves gpt-6.1-sol on chat completions without tool calling (tools only on
    # its Responses API), and gpt-6-luna's tools only with reasoning_effort "none".
    chat_only_models=frozenset({"gpt-6.1-sol"}),
    request_extras={"gpt-6-luna": {"reasoning_effort": "none"}},
)

OPENROUTER = ModelProvider(
    name="openrouter",
    base_url="https://openrouter.ai/api/v1",
    api_key_env="OPENROUTER_API_KEY",
    model_prefixes=("openrouter/",),
    # OpenRouter passes the upstream provider's price through; its credit-purchase fee
    # is not modelled. The live catalog (embry0/agents/openrouter_catalog.py) replaces
    # this list and its price once one is loaded; this entry is what a deployment that
    # never reached OpenRouter's list offers.
    models=("openrouter/openai/gpt-6.1-sol",),
    pricing_usd_per_mtok={"openrouter/openai/gpt-6.1-sol": (2.0, 10.0)},
    openai_base_url="https://openrouter.ai/api/v1",
    runtime="chat_completions",
    sandbox_key_env="EMBRY0_LLM_OPENROUTER_API_KEY",
    strip_routing_prefix=True,
    # GET /models answers without a key, so it proves nothing about one.
    probe_path="/key",
)

GOOGLE = ModelProvider(
    name="google",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    api_key_env="GEMINI_API_KEY",
    model_prefixes=("gemini-",),
    models=("gemini-3.8-flash", "gemini-4-argon"),
    # Both are introductory rates: gemini-4-argon becomes $4/$20 on 2026-12-01 and
    # gemini-3.8-flash $1.50/$7.50 on 2027-01-01.
    pricing_usd_per_mtok={"gemini-3.8-flash": (0.75, 3.75), "gemini-4-argon": (2.0, 10.0)},
    openai_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    runtime="chat_completions",
    sandbox_key_env="EMBRY0_LLM_GOOGLE_API_KEY",
)

GROQ = ModelProvider(
    name="groq",
    base_url="https://api.groq.com/openai/v1",
    api_key_env="GROQ_API_KEY",
    model_prefixes=("groq/",),
    models=("groq/openai/gpt-oss-120b", "groq/openai/gpt-oss-20b"),
    pricing_usd_per_mtok={"groq/openai/gpt-oss-120b": (0.15, 0.60), "groq/openai/gpt-oss-20b": (0.075, 0.30)},
    openai_base_url="https://api.groq.com/openai/v1",
    runtime="chat_completions",
    sandbox_key_env="EMBRY0_LLM_GROQ_API_KEY",
    strip_routing_prefix=True,
)

DEEPSEEK = ModelProvider(
    name="deepseek",
    base_url="https://api.deepseek.com",
    api_key_env="DEEPSEEK_API_KEY",
    model_prefixes=("deepseek-",),
    models=("deepseek-flash", "deepseek-v4-pro"),
    # The peak-hour rates; off-peak hours bill half, which is not modelled.
    pricing_usd_per_mtok={"deepseek-flash": (0.30, 1.20), "deepseek-v4-pro": (1.32, 3.96)},
    openai_base_url="https://api.deepseek.com",
    runtime="chat_completions",
    sandbox_key_env="EMBRY0_LLM_DEEPSEEK_API_KEY",
)

CUSTOM = ModelProvider(
    name="custom",
    base_url="",
    api_key_env="CUSTOM_LLM_API_KEY",
    model_prefixes=("custom/",),
    models=(),
    pricing_usd_per_mtok={},
    runtime="chat_completions",
    sandbox_key_env="EMBRY0_LLM_CUSTOM_API_KEY",
    base_url_env="CUSTOM_LLM_BASE_URL",
    sandbox_base_url_env="EMBRY0_LLM_CUSTOM_BASE_URL",
    strip_routing_prefix=True,
    # A server on the operator's own hardware (Ollama, vLLM, LM Studio) bills nothing
    # per token, so any model it serves is recorded at $0 instead of being refused.
    default_pricing_usd_per_mtok=(0.0, 0.0),
)

PROVIDERS: tuple[ModelProvider, ...] = (XAI,)
"""Non-Anthropic env-overlay registry — never add ANTHROPIC here."""

CATALOG_PROVIDERS: tuple[ModelProvider, ...] = (ANTHROPIC, *PROVIDERS)
"""The providers every deployment has, for catalog surfaces (GET /config/models, providers page).
``catalog_providers()`` adds the enabled OpenAI-compatible ones."""

OPENAI_COMPATIBLE: tuple[ModelProvider, ...] = (OPENAI, OPENROUTER, GOOGLE, GROQ, DEEPSEEK, CUSTOM)
"""The providers that run on OpenAICompatibleExecutor, each off until enabled."""

ALL_PROVIDERS: tuple[ModelProvider, ...] = (*CATALOG_PROVIDERS, *OPENAI_COMPATIBLE)

ENABLE_SETTING: Final = "OPENAI_COMPATIBLE_PROVIDERS"
"""Orchestrator-env setting: comma-separated names from ``OPENAI_COMPATIBLE``. Empty (the
default) enables none of them."""

_warned_settings: set[str] = set()


def enabled_openai_compatible(environ: Mapping[str, str] | None = None) -> tuple[ModelProvider, ...]:
    """The ``OPENAI_COMPATIBLE`` providers ``OPENAI_COMPATIBLE_PROVIDERS`` names, in catalog order."""
    raw = (os.environ if environ is None else environ).get(ENABLE_SETTING, "")
    names = {part.strip().lower() for part in raw.split(",") if part.strip()}
    known = {p.name for p in OPENAI_COMPATIBLE}
    unknown = sorted(names - known)
    if unknown and raw not in _warned_settings:
        _warned_settings.add(raw)
        logger.warning(
            "openai_compatible_providers_unknown", unknown=unknown, known=sorted(known), setting=ENABLE_SETTING
        )
    return tuple(p for p in OPENAI_COMPATIBLE if p.name in names)


def routed_providers(environ: Mapping[str, str] | None = None) -> tuple[ModelProvider, ...]:
    """The non-Anthropic providers a model id can route to in this deployment."""
    return (*PROVIDERS, *enabled_openai_compatible(environ))


def catalog_providers(environ: Mapping[str, str] | None = None) -> tuple[ModelProvider, ...]:
    """Every provider this deployment offers, Anthropic first."""
    return (*CATALOG_PROVIDERS, *enabled_openai_compatible(environ))


def provider_named(name: str | None) -> ModelProvider | None:
    """The provider called ``name``, enabled or not. The sandbox looks providers up by the
    name the orchestrator routed to, so it needs no copy of the setting."""
    return next((p for p in ALL_PROVIDERS if p.name == name), None)


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
    """``model``'s (input, output) $/Mtok: OpenRouter's live price, its own entry, else its provider's default rates."""
    live = catalog_rates(model)
    if live is not None:
        return live
    for provider in ALL_PROVIDERS:
        rates = provider.pricing_usd_per_mtok.get(model)
        if rates is not None:
            return rates
    for provider in ALL_PROVIDERS:
        if provider.default_pricing_usd_per_mtok is not None and any(
            model.startswith(prefix) for prefix in provider.model_prefixes
        ):
            return provider.default_pricing_usd_per_mtok
    return None


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
        "must be listed in pricing_usd_per_mtok (embry0/agents/providers.py) or, for "
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


def chat_only_message(provider: ModelProvider, model: str, agent_type: str, tools: Iterable[str]) -> str:
    """The refusal for an agent that needs tools on a model served without tool calling."""
    return (
        f"model {model!r} is chat-only on {provider.name}'s OpenAI-compatible API (no tool calling there), "
        f"and agent {agent_type!r} needs tools ({', '.join(tools)}); pick a model with tool calling "
        "(docs/providers.md)"
    )
