"""Agent config resolution — merges definition, template, and runtime overrides."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResolvedAgentConfig:
    model: str
    tools: list[str]
    skills: list[str]
    system_prompt: str


def _ensure_list(value: Any) -> list[str]:
    """Handle both JSON strings and Python lists from DB."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return list(value) if value else []


def resolve_agent_config(
    agent_type: str,
    agent_definition: dict[str, Any],
    template_config: dict[str, Any] | None = None,
    pipeline_config: dict[str, Any] | None = None,
) -> ResolvedAgentConfig:
    """Merge chain: agent_definition <- template overrides <- runtime overrides.

    For model, tools, skills: later levels replace earlier.
    system_prompt comes only from agent_definition.
    """
    model = agent_definition.get("model", "")
    tools = _ensure_list(agent_definition.get("tools", []))
    skills = _ensure_list(agent_definition.get("skills", []))
    system_prompt = agent_definition.get("system_prompt", "")

    # Template overrides
    if template_config:
        if agent_type in template_config.get("agent_models", {}):
            model = template_config["agent_models"][agent_type]
        if agent_type in template_config.get("agent_tools", {}):
            tools = template_config["agent_tools"][agent_type]
        if agent_type in template_config.get("agent_skills", {}):
            skills = template_config["agent_skills"][agent_type]

    # Runtime (triage) overrides
    if pipeline_config:
        if agent_type in pipeline_config.get("agent_models", {}):
            model = pipeline_config["agent_models"][agent_type]
        if agent_type in pipeline_config.get("agent_tools", {}):
            tools = pipeline_config["agent_tools"][agent_type]
        if agent_type in pipeline_config.get("agent_skills", {}):
            skills = pipeline_config["agent_skills"][agent_type]

    return ResolvedAgentConfig(
        model=model,
        tools=tools,
        skills=skills,
        system_prompt=system_prompt,
    )
