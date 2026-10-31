"""What a write of the settings document is checked against beyond the values themselves.

A section whose values name something the running deployment offers (a
model of its provider catalogue) checks them through pydantic's validation
context, which the settings store builds here for every write it stores:
``PUT`` and ``PATCH /config/settings``, their dry runs, and an undo. A
stored value read back, the environment import at startup and the offline
``embry0 settings check`` validate without it, so what the catalogue offers
moving (OpenRouter's live list is read after startup; a provider is turned
off) never turns a stored value into its default. Such a value is caught
where it is used: the feature logs that its model cannot be called.

The check lives here, in the orchestrator, and not in
``embry0.agents.providers``, which the sandbox images carry: it reads that
module's catalogue and changes nothing in it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from embry0.settings.sections.background_models import CATALOGUE

#: Builds the context of one write; the store's default is :func:`deployment_context`.
WriteContext = Callable[[], Mapping[str, Any]]


def model_use_problem(model: str, needs: Iterable[str]) -> str | None:
    """Why this deployment cannot run ``model`` for a use that ``needs`` some things of it, or None.

    ``model`` must be one the catalogue offers (``known_models``, or a
    ``custom/<model>`` of the operator's own server, with the custom provider
    enabled) and priced, so what it costs is counted. Each need:

    - ``tools``: its agents call tools, so not a model its provider serves
      without tool calling;
    - ``chat``: it is asked directly over chat completions, which only an
      OpenAI-compatible provider (``custom`` included) serves.

    The words never repeat ``model``: the API answers them in a 422.
    """
    from embry0.agents.providers import (
        chat_only_models,
        is_custom_model,
        known_models,
        provider_for_model,
        unpriced_models,
    )

    if model not in known_models() and not is_custom_model(model):
        return (
            "not a model this deployment offers: GET /api/v1/config/models lists them, and a model of your own "
            "server is custom/<model>, with the custom provider enabled (OPENAI_COMPATIBLE_PROVIDERS)"
        )
    if unpriced_models([model]):
        return "this model has no price, so what it spends could not be counted against the budget caps"
    wanted = set(needs)
    if "tools" in wanted and chat_only_models([model]):
        return "its provider serves it without tool calling, and the agents of this feature call tools"
    provider = provider_for_model(model)
    if "chat" in wanted and (provider is None or provider.runtime != "chat_completions"):
        return "this is asked over chat completions, which only custom/<model> or an OpenAI-compatible provider serves"
    return None


def deployment_context() -> dict[str, Any]:
    """The context every write the orchestrator stores is checked with: the provider catalogue as it is now."""
    return {CATALOGUE: model_use_problem}


def no_context() -> dict[str, Any]:
    """No deployment to check against: each value meets only its section's own rules."""
    return {}
