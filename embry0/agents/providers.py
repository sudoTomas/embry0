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


XAI = ModelProvider(
    name="xai",
    base_url="https://api.x.ai",
    api_key_env="XAI_API_KEY",
    model_prefixes=("grok-",),
    models=("grok-4.5",),
    pricing_usd_per_mtok={"grok-4.5": (2.0, 6.0)},
)

PROVIDERS: tuple[ModelProvider, ...] = (XAI,)


def provider_for_model(model: str) -> ModelProvider | None:
    """The non-Anthropic provider serving ``model``, or None for Anthropic."""
    for provider in PROVIDERS:
        if any(model.startswith(prefix) for prefix in provider.model_prefixes):
            return provider
    return None
