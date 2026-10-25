"""#465: one default model for every agent.

``provider_config.default_model`` used to be stored and read by nothing. It
is now a level of model resolution. Later wins:

1. the agent definition's ``model``
2. ``default_model``, when set
3. a pipeline template's ``agent_models``
4. a job's explicit ``agent_models``

Triage's own picks (``pipeline_config.agent_models``) apply only while no
default is set. The disabled-model tier fallback runs after all of that.
"""

from __future__ import annotations

from typing import Any

import pytest
import structlog

from embry0.agents.resolver import resolve_agent_invocation
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

_TIERS = {
    "model_heavy": "claude-opus-5",
    "model_medium": "claude-sonnet-5",
    "model_light": "claude-haiku-4-5",
}

_EVERY_AGENT_TYPE = (
    "triage",
    "developer",
    "review",
    "qa",
    "analysis",
    "research",
    "ops",
    "onboarding",
    "pr-reviewer",
    # The specialists.
    "kintsugi",
    "gardener",
    "interrogator",
    "why",
    "how",
    "blast-radius",
    "comment-sicko",
    "design-auditor",
    "context-auditor",
    "area-mapper",
)


def _policy(default_model: str = "", disabled: list[str] | None = None) -> dict[str, Any]:
    return {"default_model": default_model, "disabled_models": disabled or [], "tier_models": dict(_TIERS)}


def _resolve(
    agent_type: str,
    *,
    model_policy: dict[str, Any] | None = None,
    template_models: dict[str, str] | None = None,
    triage_models: dict[str, str] | None = None,
    job_models: dict[str, str] | None = None,
):
    # Only named when a test sets it, so the tests that do not are comparable
    # with a resolver that has no such level.
    explicit: dict[str, Any] = {"job_agent_models": job_models} if job_models is not None else {}
    return resolve_agent_invocation(
        agent_type=agent_type,
        prompt="p",
        system_context="",
        global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
        repo_prefs=None,
        job_overrides={},
        agent_definition=dict(BUILTIN_SEED[agent_type]),
        template_config={"agent_models": template_models} if template_models else None,
        pipeline_config={"agent_models": triage_models} if triage_models else {},
        max_turns=5,
        timeout_seconds=60,
        credentials={"api_key": "", "oauth_token": "tok"},
        model_policy=model_policy,
        **explicit,
    )


# ---------------------------------------------------------------------------
# The default reaches every agent type
# ---------------------------------------------------------------------------


def test_the_builtin_definitions_cover_every_agent_type() -> None:
    """Guards the parametrized tests below: a renamed or dropped type would
    otherwise shrink their coverage without failing anything."""
    assert set(_EVERY_AGENT_TYPE) == set(BUILTIN_SEED), (
        f"agent types under test {sorted(_EVERY_AGENT_TYPE)} != builtin definitions {sorted(BUILTIN_SEED)}"
    )


@pytest.mark.parametrize("agent_type", _EVERY_AGENT_TYPE)
def test_default_model_is_the_model_of_every_agent_type(agent_type: str) -> None:
    definition_model = BUILTIN_SEED[agent_type]["model"]
    assert definition_model != "grok-4.7", "the definition must name another model for this test to mean anything"

    inv = _resolve(agent_type, model_policy=_policy("grok-4.7"))

    assert inv.model == "grok-4.7", (
        f"{agent_type}: default_model=grok-4.7 must outrank the definition's {definition_model!r}, got {inv.model!r}"
    )
    assert inv.provider == "xai", f"{agent_type}: the provider overlay must follow the default model"


@pytest.mark.parametrize("agent_type", _EVERY_AGENT_TYPE)
def test_empty_default_model_leaves_the_definition_model(agent_type: str) -> None:
    definition_model = BUILTIN_SEED[agent_type]["model"]
    for policy in (None, _policy("")):
        inv = _resolve(agent_type, model_policy=policy)
        assert inv.model == definition_model, (
            f"{agent_type}: with no default the definition's {definition_model!r} must stand, got {inv.model!r}"
        )


# ---------------------------------------------------------------------------
# What outranks the default, and what does not
# ---------------------------------------------------------------------------


def test_explicit_job_model_beats_the_default() -> None:
    inv = _resolve(
        "developer",
        model_policy=_policy("grok-4.7"),
        job_models={"developer": "claude-haiku-4-5"},
    )
    assert inv.model == "claude-haiku-4-5", (
        f"a job's explicit agent_models must win over default_model, got {inv.model!r}"
    )


def test_explicit_job_model_for_another_agent_does_not_leak() -> None:
    inv = _resolve(
        "review",
        model_policy=_policy("grok-4.7"),
        job_models={"developer": "claude-haiku-4-5"},
    )
    assert inv.model == "grok-4.7", f"an override for the developer must not move the reviewer, got {inv.model!r}"


def test_template_model_beats_the_default() -> None:
    inv = _resolve(
        "review",
        model_policy=_policy("grok-4.7"),
        template_models={"review": "claude-haiku-4-5"},
    )
    assert inv.model == "claude-haiku-4-5", (
        f"a pipeline template's agent_models must win over default_model, got {inv.model!r}"
    )


def test_triage_chosen_model_does_not_beat_the_default() -> None:
    inv = _resolve(
        "developer",
        model_policy=_policy("grok-4.7"),
        triage_models={"developer": "claude-opus-5", "review": "claude-sonnet-5"},
    )
    assert inv.model == "grok-4.7", (
        f"triage's pipeline_config.agent_models must not outvote default_model, got {inv.model!r}"
    )


def test_ignored_triage_pick_is_logged() -> None:
    with structlog.testing.capture_logs() as logs:
        _resolve("developer", model_policy=_policy("grok-4.7"), triage_models={"developer": "claude-opus-5"})
    events = [entry for entry in logs if entry["event"] == "triage_model_ignored_default_model_set"]
    assert len(events) == 1, f"an overruled triage pick must leave one log line, saw {logs}"
    assert events[0]["triage_model"] == "claude-opus-5"
    assert events[0]["default_model"] == "grok-4.7"


def test_triage_chosen_model_still_applies_without_a_default() -> None:
    inv = _resolve("developer", model_policy=_policy(""), triage_models={"developer": "claude-haiku-4-5"})
    assert inv.model == "claude-haiku-4-5", (
        f"with no default, triage's pick must keep outranking the definition, got {inv.model!r}"
    )


def test_explicit_job_model_beats_triage_and_template_and_default() -> None:
    inv = _resolve(
        "developer",
        model_policy=_policy("grok-4.7"),
        template_models={"developer": "claude-sonnet-5"},
        triage_models={"developer": "claude-opus-5"},
        job_models={"developer": "claude-haiku-4-5"},
    )
    assert inv.model == "claude-haiku-4-5", f"the job's explicit model is the last word, got {inv.model!r}"


def test_explicit_job_model_beats_triage_without_a_default() -> None:
    """The pre-#465 order, now that explicit models travel separately."""
    inv = _resolve(
        "developer",
        triage_models={"developer": "claude-opus-5"},
        job_models={"developer": "claude-haiku-4-5"},
    )
    assert inv.model == "claude-haiku-4-5", f"explicit job models must still beat triage, got {inv.model!r}"


# ---------------------------------------------------------------------------
# The disabled-model fallback runs after the default is chosen
# ---------------------------------------------------------------------------


def test_disabled_default_model_falls_back_to_the_tier() -> None:
    """Unreachable through the API (PUT /config/provider refuses it), but a
    row can still hold the pair — the existing tier fallback must cover it."""
    policy = _policy("grok-4.7", disabled=["grok-4.7"])
    assert _resolve("developer", model_policy=policy).model == _TIERS["model_heavy"], (
        "a disabled default must fall back to the heavy tier for the developer"
    )
    review = _resolve("review", model_policy=policy)
    assert review.model == _TIERS["model_medium"], "a disabled default must fall back to the medium tier otherwise"
    assert review.provider is None, "the provider overlay must follow the substituted model"


# ---------------------------------------------------------------------------
# build_model_policy — one shape for the agent node, the pre-check and triage
# ---------------------------------------------------------------------------


def test_model_policy_carries_the_default_model() -> None:
    from embry0.agents.model_policy import build_model_policy

    policy = build_model_policy({**_TIERS, "default_model": " grok-4.7 ", "disabled_models": ["claude-haiku-4-5"]})
    assert policy == {
        "default_model": "grok-4.7",
        "disabled_models": ["claude-haiku-4-5"],
        "tier_models": _TIERS,
    }


def test_model_policy_ignores_a_default_outside_the_catalogue() -> None:
    """A value saved while the setting was inert (or a DEFAULT_MODEL env
    fallback) was never validated. It must not move every agent onto it."""
    from embry0.agents.model_policy import build_model_policy

    with structlog.testing.capture_logs() as logs:
        policy = build_model_policy({**_TIERS, "default_model": "not-a-model"})
    assert policy["default_model"] == "", f"an uncatalogued default must be ignored, got {policy['default_model']!r}"
    assert [entry["event"] for entry in logs] == ["default_model_unknown_ignored"], (
        f"ignoring the configured default must be logged, saw {logs}"
    )


def test_model_policy_has_no_default_for_a_bare_row() -> None:
    from embry0.agents.model_policy import build_model_policy

    assert build_model_policy({}) == {
        "default_model": "",
        "disabled_models": [],
        "tier_models": {"model_heavy": "", "model_medium": "", "model_light": ""},
    }
