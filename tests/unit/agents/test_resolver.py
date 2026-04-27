"""Tests for resolve_agent_config()."""

from __future__ import annotations

import json

from athanor.agents.resolver import ResolvedAgentConfig, resolve_agent_config

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

AGENT_TYPE = "triage"

BASE_DEFINITION = {
    "model": "gpt-4o",
    "tools": ["search", "calculator"],
    "skills": ["summarise"],
    "system_prompt": "You are a triage agent.",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_agent_definition_only():
    """No overrides — returned config mirrors definition values exactly."""
    cfg = resolve_agent_config(AGENT_TYPE, BASE_DEFINITION)

    assert isinstance(cfg, ResolvedAgentConfig)
    assert cfg.model == "gpt-4o"
    assert cfg.tools == ["search", "calculator"]
    assert cfg.skills == ["summarise"]
    assert cfg.system_prompt == "You are a triage agent."


def test_template_overrides_model():
    """template_config agent_models replaces the definition model."""
    template = {"agent_models": {AGENT_TYPE: "claude-3-5-sonnet"}}

    cfg = resolve_agent_config(AGENT_TYPE, BASE_DEFINITION, template_config=template)

    assert cfg.model == "claude-3-5-sonnet"
    # Other fields unchanged
    assert cfg.tools == ["search", "calculator"]
    assert cfg.skills == ["summarise"]


def test_template_overrides_tools():
    """template_config agent_tools replaces the definition tools."""
    template = {"agent_tools": {AGENT_TYPE: ["web_search", "code_exec"]}}

    cfg = resolve_agent_config(AGENT_TYPE, BASE_DEFINITION, template_config=template)

    assert cfg.tools == ["web_search", "code_exec"]
    assert cfg.model == "gpt-4o"


def test_template_overrides_skills():
    """template_config agent_skills replaces the definition skills."""
    template = {"agent_skills": {AGENT_TYPE: ["translate", "classify"]}}

    cfg = resolve_agent_config(AGENT_TYPE, BASE_DEFINITION, template_config=template)

    assert cfg.skills == ["translate", "classify"]


def test_runtime_overrides_template():
    """pipeline_config agent_models overrides what the template set."""
    template = {"agent_models": {AGENT_TYPE: "claude-3-5-sonnet"}}
    pipeline = {"agent_models": {AGENT_TYPE: "gpt-4o-mini"}}

    cfg = resolve_agent_config(
        AGENT_TYPE,
        BASE_DEFINITION,
        template_config=template,
        pipeline_config=pipeline,
    )

    assert cfg.model == "gpt-4o-mini"


def test_runtime_overrides_tools_and_skills():
    """pipeline_config overrides both tools and skills independently."""
    template = {
        "agent_tools": {AGENT_TYPE: ["web_search"]},
        "agent_skills": {AGENT_TYPE: ["translate"]},
    }
    pipeline = {
        "agent_tools": {AGENT_TYPE: ["code_exec", "file_io"]},
        "agent_skills": {AGENT_TYPE: ["classify"]},
    }

    cfg = resolve_agent_config(
        AGENT_TYPE,
        BASE_DEFINITION,
        template_config=template,
        pipeline_config=pipeline,
    )

    assert cfg.tools == ["code_exec", "file_io"]
    assert cfg.skills == ["classify"]


def test_system_prompt_not_overridable():
    """system_prompt is always taken from agent_definition, never from overrides."""
    template = {
        "agent_models": {AGENT_TYPE: "claude-3-5-sonnet"},
        # Even if someone tries to slip system_prompt into template it is ignored
        "system_prompt": "Injected system prompt from template",
    }
    pipeline = {
        "system_prompt": "Injected system prompt from pipeline",
    }

    cfg = resolve_agent_config(
        AGENT_TYPE,
        BASE_DEFINITION,
        template_config=template,
        pipeline_config=pipeline,
    )

    assert cfg.system_prompt == "You are a triage agent."


def test_no_override_for_different_agent():
    """Overrides keyed under 'developer' do not affect 'validator' resolution."""
    template = {
        "agent_models": {"developer": "gpt-4o-mini"},
        "agent_tools": {"developer": ["code_exec"]},
    }
    pipeline = {
        "agent_skills": {"developer": ["lint"]},
    }

    cfg = resolve_agent_config(
        "validator",
        BASE_DEFINITION,
        template_config=template,
        pipeline_config=pipeline,
    )

    # None of the 'developer' overrides should bleed through
    assert cfg.model == "gpt-4o"
    assert cfg.tools == ["search", "calculator"]
    assert cfg.skills == ["summarise"]


def test_empty_overrides():
    """None for both template_config and pipeline_config returns definition values."""
    cfg = resolve_agent_config(
        AGENT_TYPE,
        BASE_DEFINITION,
        template_config=None,
        pipeline_config=None,
    )

    assert cfg.model == "gpt-4o"
    assert cfg.tools == ["search", "calculator"]
    assert cfg.skills == ["summarise"]
    assert cfg.system_prompt == "You are a triage agent."


def test_jsonb_tools_parsed():
    """tools and skills stored as JSON strings (from DB JSONB column) are parsed."""
    definition_with_jsonb = {
        "model": "gpt-4o",
        "tools": json.dumps(["search", "calculator"]),
        "skills": json.dumps(["summarise"]),
        "system_prompt": "You are a triage agent.",
    }

    cfg = resolve_agent_config(AGENT_TYPE, definition_with_jsonb)

    assert cfg.tools == ["search", "calculator"]
    assert cfg.skills == ["summarise"]
    assert isinstance(cfg.tools, list)
    assert isinstance(cfg.skills, list)
