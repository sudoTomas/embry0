"""Model policy — the provider_config settings applied to every agent run.

One shape, built in one place, so the agent node, the LLM pre-check and the
triage prompt cannot disagree about which model the deployment wants:

    {"default_model": str,          # "" = no deployment-wide default
     "disabled_models": [str, ...],
     "tier_models": {"model_heavy": str, "model_medium": str, "model_light": str}}

Consumed by ``embry0.agents.resolver.resolve_agent_invocation``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import structlog

from embry0.agents.providers import known_models

logger = structlog.get_logger(__name__)

TIER_KEYS: tuple[str, ...] = ("model_heavy", "model_medium", "model_light")


def usable_default_model(raw: Any) -> str:
    """The configured default model, or "" when there is none to apply.

    ``PUT /config/provider`` refuses an id outside the catalogue, but a value
    can still arrive unvalidated: a row saved while the setting was inert, or
    the ``DEFAULT_MODEL`` env fallback of a deployment with no row. Such a
    value is ignored rather than run — one stale string must not move every
    agent of every job onto a model nobody chose.
    """
    model = raw.strip() if isinstance(raw, str) else ""
    if not model:
        return ""
    if model not in known_models():
        logger.warning("default_model_unknown_ignored", default_model=model)
        return ""
    return model


def build_model_policy(provider_config: Mapping[str, Any]) -> dict[str, Any]:
    """The model policy held by one provider_config row (or its env defaults)."""
    disabled = provider_config.get("disabled_models")
    return {
        "default_model": usable_default_model(provider_config.get("default_model")),
        "disabled_models": list(disabled) if isinstance(disabled, (list, tuple, set, frozenset)) else [],
        "tier_models": {tier: provider_config.get(tier) or "" for tier in TIER_KEYS},
    }


def resolve_tier_models(provider_config: Mapping[str, Any] | None, app_config: Any) -> dict[str, str]:
    """Each tier's model as runs read it, so every tier names one.

    The provider_config row (None when it could not be read), then
    ``Embry0Config`` (``MODEL_HEAVY`` and the rest), then the config class
    default. Triage names these tiers, and every pr-review job runs on the
    heavy one.
    """
    from embry0.config import Embry0Config

    policy = build_model_policy(provider_config or {})
    tiers: dict[str, str] = {}
    for tier in TIER_KEYS:
        configured = policy["tier_models"][tier] or getattr(app_config, tier, None)
        tiers[tier] = (
            configured if isinstance(configured, str) and configured else Embry0Config.model_fields[tier].default
        )
    return tiers
