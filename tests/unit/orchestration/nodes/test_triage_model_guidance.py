"""#465: triage's model guidance is built from configuration, not literals.

The prompt used to say ``ALWAYS use "claude-opus-5" for the developer`` and
gave Claude ids for review. Triage's ``pipeline_config.agent_models`` is a
level of model resolution, so every triaged job ran on those ids whatever
the operator had configured. The guidance now names the configured tier
models, or — when the deployment sets one default model — tells triage not
to choose models at all.
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from embry0.agents.providers import known_models
from embry0.config import Embry0Config

# The ids the prompt hardcoded before #465.
_OLD_LITERALS = ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5")

# Deliberately none of the config defaults, so a prompt that names them can
# only have read them from the tier settings it was given.
_TIERS = {
    "model_heavy": "tier-heavy-model",
    "model_medium": "tier-medium-model",
    "model_light": "tier-light-model",
}

_MODEL_ID = re.compile(r"\b(?:claude|grok)-[a-z0-9]")


def _prompt(default_model: str = "", tiers: dict[str, str] | None = None) -> str:
    from embry0.orchestration.nodes.triage import build_triage_system_prompt
    from embry0.settings.sections.done_when import DONE_WHEN

    return build_triage_system_prompt(
        default_model=default_model,
        tier_models=tiers if tiers is not None else _TIERS,
        ticket_sections=DONE_WHEN.defaults().ticket_sections,
    )


# ---------------------------------------------------------------------------
# The prompt text
# ---------------------------------------------------------------------------


def test_guidance_names_the_configured_tier_models() -> None:
    prompt = _prompt()

    assert 'ALWAYS use "tier-heavy-model" for the developer agent' in prompt, (
        "the developer must be pointed at the configured heavy tier"
    )
    assert 'For the review agent, "tier-medium-model" is the default' in prompt, (
        "review must default to the configured medium tier"
    )
    assert 'tier\n  the review down to "tier-light-model"' in prompt, (
        "routine review must tier down to the configured light tier"
    )
    assert '{"developer": "tier-heavy-model", "review": "tier-medium-model"}' in prompt, (
        "the agent_models example must use the configured tiers"
    )
    for literal in _OLD_LITERALS:
        assert literal not in prompt, f"the triage prompt still hardcodes {literal!r}"


def test_guidance_with_a_default_model_tells_triage_not_to_choose() -> None:
    prompt = _prompt(default_model="grok-4.7")

    assert "Do NOT choose models" in prompt
    assert "Omit `agent_models`" in prompt
    assert "ALWAYS use" not in prompt, "the tier guidance must be gone when a default model is set"
    named = sorted(m for m in (*known_models(), *_TIERS.values()) if m in prompt)
    assert named == [], f"with a default model the prompt must name no model id, found {named}"
    stray = _MODEL_ID.findall(prompt)
    assert stray == [], f"with a default model the prompt must name no model id, found {stray}"


@pytest.mark.parametrize("default_model", ["", "grok-4.7"])
def test_the_guidance_slot_is_filled_exactly_once(default_model: str) -> None:
    from embry0.orchestration.nodes.triage import _MODEL_GUIDANCE_SLOT, _TRIAGE_SYSTEM_PROMPT_TEMPLATE

    assert _TRIAGE_SYSTEM_PROMPT_TEMPLATE.count(_MODEL_GUIDANCE_SLOT) == 1
    prompt = _prompt(default_model=default_model)
    assert _MODEL_GUIDANCE_SLOT not in prompt, "the model-guidance marker leaked into the prompt"
    assert prompt.count("Model selection") == 2, "one section heading plus the pipeline_config cross-reference"
    assert prompt.rstrip().endswith("no markdown fences or extra text."), "the rest of the prompt must survive"


def test_the_template_itself_names_no_model() -> None:
    from embry0.orchestration.nodes.triage import _TRIAGE_SYSTEM_PROMPT_TEMPLATE

    stray = _MODEL_ID.findall(_TRIAGE_SYSTEM_PROMPT_TEMPLATE)
    assert stray == [], f"a model id is written into the triage prompt template: {stray}"


def test_tier_guidance_refuses_a_missing_tier() -> None:
    with pytest.raises(ValueError, match="model_light"):
        _prompt(tiers={"model_heavy": "a", "model_medium": "b", "model_light": ""})


# ---------------------------------------------------------------------------
# Where triage_node gets the settings
# ---------------------------------------------------------------------------


class _ProviderRowDb:
    """A DatabasePool double holding one provider_config row and nothing else."""

    def __init__(self, provider_row: dict[str, Any] | None) -> None:
        self._provider_row = provider_row

    async def fetchrow(self, sql: str, *_args: Any) -> dict[str, Any] | None:
        return self._provider_row if "provider_config" in sql else None


_STATE = {
    "job_id": "job-1",
    "issue_id": "iss-1",
    "repo": "acme/backend",
    "task": "ENG-1: fix the thing",
    "sandbox_container_id": "c1",
    "agent_outputs": [],
    "errors": [],
}


async def _triage_system_prompt(configurable: dict[str, Any]) -> str:
    from embry0.workflows.issue_to_pr.nodes import triage_node

    captured: dict[str, Any] = {}

    async def _run(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        captured["agent_definition"] = kwargs.get("agent_definition")
        return {
            "agent_outputs": [
                {
                    "agent_type": "triage",
                    "is_error": False,
                    "output": '{"action": "proceed", "reasoning": "ok", "confidence": 0.9}',
                }
            ],
            "total_cost_usd": 0.0,
        }

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        await triage_node(
            dict(_STATE),
            {"configurable": {"agent_runner": object(), "credentials": {}, **configurable}},
        )
    return str(captured["agent_definition"]["system_prompt"])


@pytest.mark.asyncio
async def test_triage_node_reads_the_tiers_from_the_provider_row() -> None:
    db = _ProviderRowDb({**_TIERS, "default_model": "", "disabled_models": []})

    prompt = await _triage_system_prompt({"db": db})

    for tier_model in _TIERS.values():
        assert tier_model in prompt, f"the provider row's {tier_model!r} must reach the triage prompt"
    for literal in _OLD_LITERALS:
        assert literal not in prompt, f"the triage prompt names {literal!r}, which the provider row does not"


@pytest.mark.asyncio
async def test_triage_node_with_a_default_model_asks_for_no_model_choice() -> None:
    db = _ProviderRowDb({**_TIERS, "default_model": "grok-4.7", "disabled_models": []})

    prompt = await _triage_system_prompt({"db": db})

    assert "Do NOT choose models" in prompt, "with default_model set, triage must be told not to choose models"
    stray = _MODEL_ID.findall(prompt)
    assert stray == [], f"with a default model the triage prompt must name no model id, found {stray}"
    for tier_model in _TIERS.values():
        assert tier_model not in prompt


@pytest.mark.asyncio
async def test_triage_node_falls_back_to_the_deployment_config_tiers() -> None:
    """A row with an empty tier resolves like GET /config/models: Embry0Config next."""
    db = _ProviderRowDb({"model_heavy": "", "model_medium": None, "default_model": ""})
    app_config = Embry0Config(
        _env_file=None,
        model_heavy="config-heavy-model",
        model_medium="config-medium-model",
        model_light="config-light-model",
    )

    prompt = await _triage_system_prompt({"db": db, "embry0_config": app_config})

    assert 'ALWAYS use "config-heavy-model"' in prompt, "an empty row tier must fall back to Embry0Config.model_heavy"
    assert '"config-medium-model" is the default' in prompt
    assert '"config-light-model"' in prompt


@pytest.mark.asyncio
async def test_triage_node_without_a_db_names_the_config_defaults() -> None:
    prompt = await _triage_system_prompt({})

    fields = Embry0Config.model_fields
    assert f'ALWAYS use "{fields["model_heavy"].default}"' in prompt
    assert f'"{fields["model_medium"].default}" is the default' in prompt
    assert f'"{fields["model_light"].default}"' in prompt


@pytest.mark.asyncio
async def test_unreadable_provider_row_does_not_block_triage() -> None:
    class _BrokenDb:
        async def fetchrow(self, *_args: Any) -> None:
            raise RuntimeError("db down")

    prompt = await _triage_system_prompt({"db": _BrokenDb()})

    assert f'ALWAYS use "{Embry0Config.model_fields["model_heavy"].default}"' in prompt
