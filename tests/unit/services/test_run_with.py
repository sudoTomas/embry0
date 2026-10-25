"""Run with (spec 2026-10-25): how a person's choice of model and key rides on a job."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from embry0.agents.resolver import resolve_agent_config
from embry0.services.run_with import (
    RunWith,
    apply_run_with,
    job_pins_reader,
    key_scopes,
    model_provider,
    models_with,
    pins_of,
    run_with_of,
)

CHOSEN = RunWith(model="openrouter/qwen3", credential_id="cred-1", provider="openrouter", credential_label="team key")


def test_the_usual_run_changes_nothing():
    assert apply_run_with(None, None) is None and apply_run_with(None, RunWith()) is None
    assert apply_run_with({"max_budget_usd": 2}, RunWith()) == {"max_budget_usd": 2}
    assert models_with({"qa": "m"}, None) == {"qa": "m"} and models_with(None, RunWith()) is None
    assert run_with_of({"run_with": {}}) is None and pins_of(None) == {}


def test_the_choice_rides_on_the_job_and_reads_back():
    config = apply_run_with({"agent_models_override": {"onboarding": "claude-haiku-4-5"}}, CHOSEN)
    assert config == {
        "agent_models_override": {"*": "openrouter/qwen3", "onboarding": "claude-haiku-4-5"},
        "run_with": {
            "model": "openrouter/qwen3",
            "credential_id": "cred-1",
            "provider": "openrouter",
            "credential_label": "team key",
        },
    }
    assert run_with_of(config) == CHOSEN and pins_of(config) == {"openrouter": "cred-1"}
    assert pins_of(apply_run_with(None, RunWith(model="m"))) == {}, "a model alone pins no key"
    assert models_with({"qa": "m"}, CHOSEN) == {"*": "openrouter/qwen3", "qa": "m"}


def test_every_agent_runs_the_chosen_model_unless_the_job_names_its_own():
    definition = {"model": "claude-sonnet-5", "tools": [], "skills": [], "system_prompt": ""}
    chosen = {"*": "openrouter/qwen3", "qa": "claude-opus-5"}
    assert resolve_agent_config("developer", definition, job_agent_models=chosen).model == "openrouter/qwen3"
    assert resolve_agent_config("qa", definition, job_agent_models=chosen).model == "claude-opus-5"
    assert resolve_agent_config("developer", definition).model == "claude-sonnet-5"


def test_a_job_on_a_repository_may_use_its_own_its_owner_s_and_the_global_keys():
    assert key_scopes("Acme/App") == [
        ("repo", "acme/app"),
        ("owner", "github:acme"),
        ("owner", "gitlab:acme"),
        ("global", ""),
    ]
    assert key_scopes(None) == [("global", "")]


def test_a_claude_model_runs_on_anthropic(monkeypatch: pytest.MonkeyPatch):
    assert model_provider("claude-sonnet-5") == "anthropic"
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "openrouter")
    assert model_provider("openrouter/qwen3") == "openrouter"


async def test_the_resolver_reads_a_job_s_pins_by_its_id():
    rows = {"job-1": {"pipeline_config": apply_run_with(None, CHOSEN)}}

    async def get(job_id):
        return rows.get(job_id)

    read = job_pins_reader(SimpleNamespace(get=get))
    assert await read("job-1") == {"openrouter": "cred-1"} and await read("job-404") == {}
