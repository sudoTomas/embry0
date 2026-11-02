"""Tests for resolve_agent_config().

The tool names are the tool catalog's: resolve_agent_config drops any other
(embry0.agents.resolver.gated_tools).
"""

from __future__ import annotations

import json

from embry0.agents.resolver import ResolvedAgentConfig, resolve_agent_config

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

AGENT_TYPE = "triage"

BASE_DEFINITION = {
    "model": "gpt-4o",
    "tools": ["Grep", "Glob"],
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
    assert cfg.tools == ["Grep", "Glob"]
    assert cfg.skills == ["summarise"]
    assert cfg.system_prompt == "You are a triage agent."


def test_template_overrides_model():
    """template_config agent_models replaces the definition model."""
    template = {"agent_models": {AGENT_TYPE: "claude-3-5-sonnet"}}

    cfg = resolve_agent_config(AGENT_TYPE, BASE_DEFINITION, template_config=template)

    assert cfg.model == "claude-3-5-sonnet"
    # Other fields unchanged
    assert cfg.tools == ["Grep", "Glob"]
    assert cfg.skills == ["summarise"]


def test_template_overrides_tools():
    """template_config agent_tools replaces the definition tools."""
    template = {"agent_tools": {AGENT_TYPE: ["WebSearch", "Bash"]}}

    cfg = resolve_agent_config(AGENT_TYPE, BASE_DEFINITION, template_config=template)

    assert cfg.tools == ["WebSearch", "Bash"]
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


def test_runtime_narrows_skills_and_tools():
    """pipeline_config's skills and tools only narrow the template's lists."""
    template = {
        "agent_tools": {AGENT_TYPE: ["WebSearch", "Bash"]},
        "agent_skills": {AGENT_TYPE: ["translate", "classify"]},
    }
    pipeline = {
        "agent_tools": {AGENT_TYPE: ["Bash", "Write"]},
        "agent_skills": {AGENT_TYPE: ["classify"]},
    }

    cfg = resolve_agent_config(
        AGENT_TYPE,
        BASE_DEFINITION,
        template_config=template,
        pipeline_config=pipeline,
    )

    assert cfg.tools == ["Bash"], f"triage's tools must only narrow the template's: {cfg.tools}"
    assert cfg.skills == ["classify"], f"triage's skills must only narrow the template's: {cfg.skills}"


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
        "agent_tools": {"developer": ["Bash"]},
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
    assert cfg.tools == ["Grep", "Glob"]
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
    assert cfg.tools == ["Grep", "Glob"]
    assert cfg.skills == ["summarise"]
    assert cfg.system_prompt == "You are a triage agent."


def test_jsonb_tools_parsed():
    """tools and skills stored as JSON strings (from DB JSONB column) are parsed."""
    definition_with_jsonb = {
        "model": "gpt-4o",
        "tools": json.dumps(["Grep", "Glob"]),
        "skills": json.dumps(["summarise"]),
        "system_prompt": "You are a triage agent.",
    }

    cfg = resolve_agent_config(AGENT_TYPE, definition_with_jsonb)

    assert cfg.tools == ["Grep", "Glob"]
    assert cfg.skills == ["summarise"]
    assert isinstance(cfg.tools, list)
    assert isinstance(cfg.skills, list)


def test_the_template_delta_applies_and_triage_adds_nothing() -> None:
    definition = {"model": "m", "tools": [], "skills": ["s:a", "s:b"], "system_prompt": ""}
    resolved = resolve_agent_config(
        "developer",
        definition,
        template_config={"agent_skills": {"developer": {"add": ["s:c"], "remove": ["s:a"]}}},
        pipeline_config={"agent_skills": {"developer": {"add": ["s:d"], "remove": ["s:b"]}}},
    )
    assert resolved.skills == ["s:c"], f"the template's delta applies, triage's only removes: {resolved.skills}"


def test_legacy_list_skill_override_still_replaces_in_a_template() -> None:
    definition = {"model": "m", "tools": [], "skills": ["s:a", "s:b"], "system_prompt": ""}
    resolved = resolve_agent_config("developer", definition, template_config={"agent_skills": {"developer": ["s:z"]}})
    assert resolved.skills == ["s:z"], resolved.skills
