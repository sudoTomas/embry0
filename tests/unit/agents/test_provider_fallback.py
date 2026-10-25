"""Provider fallback (spec 2026-10-25): the model an agent runs on when its own model's account is paused."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from embry0.agents.fallback import (
    ModelFallback,
    Pause,
    describe,
    fallback_order,
    fallbacks_from_config,
    paused_by_outputs,
    replan,
    runnable_fallback_models,
    substitute,
)
from embry0.agents.resolver import resolve_agent_invocation
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

UNTIL = datetime(2026, 10, 26, tzinfo=UTC)
ANTHROPIC_PAUSED = Pause("anthropic", "quota_exhausted", UNTIL)
TO_GROK = {"anthropic": ModelFallback.for_pause(ANTHROPIC_PAUSED, "grok-4.7")}


@pytest.fixture(autouse=True)
def _openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "openrouter")


def _resolve(agent_type: str, *, fallbacks: Any = None, job_models: dict[str, str] | None = None, **kw: Any):
    return resolve_agent_invocation(
        agent_type=agent_type,
        prompt="p",
        system_context="",
        global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
        repo_prefs=None,
        job_overrides={},
        agent_definition=dict(BUILTIN_SEED[agent_type]),
        pipeline_config={},
        max_turns=10,
        timeout_seconds=600,
        credentials={"api_key": "", "oauth_token": "tok"},
        model_policy=kw.get("model_policy"),
        job_agent_models=job_models,
        model_fallbacks=fallbacks,
    )


def test_the_order_is_cleaned_and_only_runnable_models_stay() -> None:
    assert fallback_order({"fallback_models": [" grok-4.7 ", "", "grok-4.7", 3, "claude-haiku-4-5"]}) == [
        "grok-4.7",
        "claude-haiku-4-5",
    ]
    assert fallback_order(None) == [] and fallback_order({"fallback_models": "grok-4.7"}) == []
    order = ["not-a-model", "grok-4.5", "grok-4.7", "openrouter/openai/gpt-6.1-sol"]
    assert runnable_fallback_models(order, disabled=["grok-4.5"]) == ["grok-4.7", "openrouter/openai/gpt-6.1-sol"]


def test_a_paused_provider_s_model_runs_on_the_plan_s_model() -> None:
    inv = _resolve("developer", fallbacks=TO_GROK)
    assert (inv.model, inv.provider) == ("grok-4.7", "xai")
    assert inv.fallback == {
        "instead_of": "claude-opus-5",
        "provider": "anthropic",
        "reason": "quota_exhausted",
        "until": UNTIL.isoformat(),
    }
    # The checks after it are the stand-in's: xAI's turn and time factors apply.
    plain = _resolve("developer", fallbacks={})
    assert (plain.model, plain.fallback) == ("claude-opus-5", None)
    assert inv.max_turns >= plain.max_turns and inv.timeout_seconds >= plain.timeout_seconds


def test_no_plan_means_no_substitution() -> None:
    for fallbacks in (None, {}):
        inv = _resolve("review", fallbacks=fallbacks)
        assert (inv.model, inv.fallback) == ("claude-sonnet-5", None)
    assert substitute("grok-4.7", TO_GROK) is None, "a model of a provider that is not paused stays"


def test_a_model_the_job_names_is_never_replaced() -> None:
    named = _resolve("developer", fallbacks=TO_GROK, job_models={"developer": "claude-haiku-4-5"})
    assert (named.model, named.fallback) == ("claude-haiku-4-5", None)
    run_with = _resolve("review", fallbacks=TO_GROK, job_models={"*": "claude-sonnet-5"})
    assert (run_with.model, run_with.fallback) == ("claude-sonnet-5", None), "Run with's model stays"
    other = _resolve("review", fallbacks=TO_GROK, job_models={"developer": "claude-haiku-4-5"})
    assert other.model == "grok-4.7", "a model named for another agent does not hold this one"


def test_the_pr_reviewer_s_per_job_model_is_the_executor_s_and_moves() -> None:
    inv = _resolve("pr-reviewer", fallbacks=TO_GROK, job_models={"pr-reviewer": "claude-opus-5"})
    assert inv.model == "grok-4.7"


def test_the_plan_round_trips_through_the_graph_config() -> None:
    raw = {provider: fb.to_config() for provider, fb in TO_GROK.items()}
    assert fallbacks_from_config(raw) == TO_GROK
    assert fallbacks_from_config({"anthropic": {"model": ""}, "xai": "junk"}) == {}
    assert fallbacks_from_config(None) == {}


def test_a_failed_run_pauses_its_provider_until_one_on_it_succeeds() -> None:
    outputs = [
        {"provider": "anthropic", "error_code": "ERR_PROVIDER_QUOTA_EXHAUSTED"},
        {"provider": "xai", "error_code": "ERR_PROVIDER_RATE_LIMITED", "provider_resets_at": UNTIL.isoformat()},
        {"provider": "openrouter", "error_code": "ERR_TURN_LIMIT"},
    ]
    paused = paused_by_outputs(outputs)
    assert set(paused) == {"anthropic", "xai"}
    assert paused["xai"] == Pause("xai", "rate_limited", UNTIL)
    assert paused_by_outputs([*outputs, {"provider": "anthropic", "error_code": None}]).keys() == {"xai"}


def test_a_mid_run_pause_moves_only_to_a_provider_the_sandbox_holds() -> None:
    order = ["grok-4.7", "openrouter/openai/gpt-6.1-sol"]
    paused_now = {"anthropic": ANTHROPIC_PAUSED}
    assert replan({}, paused_now, order, ready=["anthropic", "openrouter"])["anthropic"].model == (
        "openrouter/openai/gpt-6.1-sol"
    ), "xai's key is not in the sandbox, so the next model of the order"
    assert replan({}, paused_now, order, ready=["anthropic"]) == {}, "no stand-in whose key is there"


def test_a_planned_stand_in_that_fails_moves_to_the_next() -> None:
    order = ["grok-4.7", "openrouter/openai/gpt-6.1-sol"]
    plan = replan(TO_GROK, {"xai": Pause("xai", "rate_limited")}, order, ready=["anthropic", "xai", "openrouter"])
    assert plan["anthropic"].model == "openrouter/openai/gpt-6.1-sol"
    assert plan["anthropic"].reason == "quota_exhausted", "the pause it stands in for is still anthropic's"
    assert plan["xai"].model == "openrouter/openai/gpt-6.1-sol"
    assert replan(TO_GROK, {}, order, ready=["anthropic", "xai"]) == TO_GROK, "the pre-check's plan stands"
    resumed = replan(TO_GROK, {}, order, ready=["anthropic", "openrouter"])
    assert resumed["anthropic"].model == "openrouter/openai/gpt-6.1-sol", (
        "a sandbox from before the pause lacks xai's key: the next model whose key it holds"
    )
    assert replan(TO_GROK, {}, order, ready=["anthropic"]) == {}, "no stand-in whose key is there"


def test_the_event_says_which_model_ran_and_why() -> None:
    record = TO_GROK["anthropic"].trace_record("claude-opus-5")
    assert describe("developer", "grok-4.7", record) == (
        "developer runs on grok-4.7 instead of claude-opus-5: "
        "the anthropic account is paused (quota_exhausted) until 2026-10-26 00:00 UTC."
    )
    assert describe("developer", "grok-4.7", {**record, "until": None}).endswith("(quota_exhausted).")
