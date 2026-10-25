"""#465: a triaged job's resolved models, through run_agent_node.

The node is where the three sources meet: the provider settings' default
model (read from the provider_config row), triage's picks
(``state["pipeline_config"]["agent_models"]``) and the job's explicit models
(``state["agent_models_override"]``). The last two used to be folded into one
map before the resolver saw them; a default model has to be able to tell
them apart — it overrides triage, and the job's explicit models override it.
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.agents.executor import AgentOutput
from embry0.orchestration.nodes.agent import load_model_policy, run_agent_node
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

_TIERS = {
    "model_heavy": "claude-opus-5",
    "model_medium": "claude-sonnet-5",
    "model_light": "claude-haiku-4-5",
}

# What triage of a pre-#465 prompt would have chosen for the job.
_TRIAGE_PICKS = {"developer": "claude-opus-5", "review": "claude-sonnet-5"}


class _ProviderRowDb:
    """A DatabasePool double holding one provider_config row and nothing else."""

    def __init__(self, provider_row: dict[str, Any] | None) -> None:
        self._provider_row = provider_row

    async def fetchrow(self, sql: str, *_args: Any) -> dict[str, Any] | None:
        return self._provider_row if "provider_config" in sql else None


def _config(default_model: str, disabled: list[str] | None = None) -> dict[str, Any]:
    row = {**_TIERS, "default_model": default_model, "disabled_models": disabled or []}
    return {"configurable": {"db": _ProviderRowDb(row)}}


async def _resolved_model(agent_type: str, *, config: dict[str, Any], state: dict[str, Any] | None = None) -> str:
    captured: dict[str, Any] = {}

    async def _capture(**kwargs: Any) -> AgentOutput:
        captured["model"] = kwargs["config"]["model"]
        captured["provider"] = kwargs["config"]["provider"]
        return AgentOutput(agent_type=agent_type, output="done", cost_usd=0.0)

    class _Runner:
        run = staticmethod(_capture)

    await run_agent_node(
        state={
            "sandbox_container_id": "c1",
            "total_cost_usd": 0.0,
            "pipeline_config": {"agent_models": dict(_TRIAGE_PICKS)},
            **(state or {}),
        },
        agent_runner=_Runner(),
        agent_type=agent_type,
        prompt="do it",
        agent_definition=dict(BUILTIN_SEED[agent_type]),
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config=config,
    )
    assert "model" in captured, f"{agent_type}: the agent never reached the runner"
    return str(captured["model"])


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_type", ["triage", "developer", "review", "qa", "analysis"])
async def test_triaged_job_runs_every_agent_on_the_default_model(agent_type: str) -> None:
    model = await _resolved_model(agent_type, config=_config("grok-4.7"))

    assert model == "grok-4.7", (
        f"{agent_type}: with default_model=grok-4.7 a triaged job must run it on grok-4.7 "
        f"(definition {BUILTIN_SEED[agent_type]['model']!r}, triage picks {_TRIAGE_PICKS}), got {model!r}"
    )


@pytest.mark.asyncio
async def test_explicit_job_model_beats_the_default_on_a_triaged_job() -> None:
    state = {"agent_models_override": {"developer": "claude-haiku-4-5"}}

    developer = await _resolved_model("developer", config=_config("grok-4.7"), state=state)
    review = await _resolved_model("review", config=_config("grok-4.7"), state=state)

    assert developer == "claude-haiku-4-5", f"the job's explicit developer model must win, got {developer!r}"
    assert review == "grok-4.7", f"an agent the job did not name stays on the default, got {review!r}"


@pytest.mark.asyncio
async def test_without_a_default_triage_picks_and_job_models_resolve_as_before() -> None:
    config = _config("")

    assert await _resolved_model("developer", config=config) == "claude-opus-5"
    assert await _resolved_model("review", config=config) == "claude-sonnet-5"
    assert await _resolved_model("qa", config=config) == BUILTIN_SEED["qa"]["model"]
    explicit = await _resolved_model(
        "developer", config=config, state={"agent_models_override": {"developer": "claude-haiku-4-5"}}
    )
    assert explicit == "claude-haiku-4-5", f"explicit job models must still beat triage, got {explicit!r}"


@pytest.mark.asyncio
async def test_load_model_policy_carries_the_default_model() -> None:
    """A row that only sets a default disables nothing — it must still load."""
    policy = await load_model_policy(_config("grok-4.7"))

    assert policy is not None, "a default model with nothing disabled must still produce a policy"
    assert policy["default_model"] == "grok-4.7"
    assert policy["tier_models"] == _TIERS


@pytest.mark.asyncio
async def test_load_model_policy_is_none_when_the_row_sets_nothing() -> None:
    assert await load_model_policy(_config("")) is None
    assert await load_model_policy({"configurable": {}}) is None
    assert await load_model_policy(None) is None


@pytest.mark.asyncio
async def test_load_model_policy_still_carries_disabled_models() -> None:
    policy = await load_model_policy(_config("", disabled=["grok-4.5"]))

    assert policy == {
        "default_model": "",
        "disabled_models": ["grok-4.5"],
        "tier_models": _TIERS,
        "fallback_models": [],
    }
