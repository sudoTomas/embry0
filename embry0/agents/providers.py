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

Caveats (documented on EMB-36):
- ``cache_control``/prompt-cache behavior on the compat layer is xAI's,
  not Anthropic's — treat cache_read/cache_creation trace columns as
  best-effort for grok runs (usage parsing is tolerant: absent fields
  become 0).
- grok-4.5 is unavailable to EU API consoles.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelProvider:
    name: str
    base_url: str
    api_key_env: str
    """Container-env var holding this provider's API key."""
    model_prefixes: tuple[str, ...]
    models: tuple[str, ...]
    """Known model ids, surfaced by GET /config/models."""
    pricing_usd_per_mtok: dict[str, tuple[float, float]]
    """model -> (input $/Mtok, output $/Mtok) — catalog metadata only."""
    turn_budget_factor: float = 1.0
    """Multiplier on an agent's ``max_turns`` when this provider serves it."""
    timeout_factor: float = 1.0
    """Multiplier on an agent's ``timeout_seconds`` when this provider serves it."""


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

PROVIDERS: tuple[ModelProvider, ...] = (XAI,)
"""Non-Anthropic env-overlay registry — never add ANTHROPIC here."""

CATALOG_PROVIDERS: tuple[ModelProvider, ...] = (ANTHROPIC, *PROVIDERS)
"""Every provider, for catalog surfaces (GET /config/models, providers page)."""


def provider_for_model(model: str) -> ModelProvider | None:
    """The non-Anthropic provider serving ``model``, or None for Anthropic."""
    for provider in PROVIDERS:
        if any(model.startswith(prefix) for prefix in provider.model_prefixes):
            return provider
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
) -> float | None:
    """Estimated USD for one message's usage, or None when the model is unpriced.

    turn_usage events carry this so cost can tick during a run; cost_update
    (the provider's own total at the end of the run) stays authoritative.
    """
    for provider in CATALOG_PROVIDERS:
        rates = provider.pricing_usd_per_mtok.get(model)
        if rates is not None:
            rate_in, rate_out = rates
            per_mtok = (
                input_tokens * rate_in
                + output_tokens * rate_out
                + cache_read_tokens * rate_in * CACHE_READ_FACTOR
                + cache_creation_tokens * rate_in * CACHE_WRITE_FACTOR
            )
            return per_mtok / 1_000_000
    return None


def known_models() -> set[str]:
    """Every model id any catalog provider declares."""
    return {m for provider in CATALOG_PROVIDERS for m in provider.models}


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
        provider = provider_for_model(model)
        if provider is not None and model not in provider.pricing_usd_per_mtok:
            found.add(model)
    return sorted(found)


def unpriced_models_message(models: Iterable[str]) -> str:
    """The operator-facing refusal for ``unpriced_models``' result."""
    return (
        f"model(s) have no price entry: {', '.join(models)} — a provider-routed model "
        "must be listed in pricing_usd_per_mtok (embry0/agents/providers.py), or its "
        "runs would be metered at $0"
    )
