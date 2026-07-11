"""Agent config resolution — merges definition, template, and runtime overrides."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from embry0.agents.invocation import AgentInvocation
from embry0.execution.auth_provider import AuthConfigError
from embry0.safety.error_codes import ErrorCode
from embry0.safety.policy import default_policy_for_agent


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


_VALID_EXECUTION_MODES = {"sdk", "cli"}
_VALID_AUTH_MODES = {"api_key", "oauth"}
# Phase 1 only supports sdk mode. Phase 2 removes this set.
_PHASE1_ALLOWED_EXECUTION_MODES = {"sdk"}


def _pick(*values: Any) -> Any:
    """Return the last non-None value. Empty lists/dicts are NOT treated as None."""
    last = None
    for v in values:
        if v is not None:
            last = v
    return last


def resolve_agent_invocation(
    *,
    agent_type: str,
    prompt: str,
    system_context: str,
    global_defaults: dict[str, Any],
    repo_prefs: dict[str, Any] | None,
    job_overrides: dict[str, Any] | None,
    agent_definition: dict[str, Any],
    pipeline_config: dict[str, Any],
    max_turns: int,
    timeout_seconds: int,
    credentials: dict[str, str],
) -> AgentInvocation:
    """Resolve a fully-validated AgentInvocation from the five-level chain.

    Precedence (lowest -> highest):
    1. global_defaults: {'execution_mode': str, 'auth_mode': str}
    2. repo_prefs (optional): {'execution_mode': str | None, 'auth_mode': str | None}
    3. job_overrides (optional): same shape
    4. agent_definition: includes 'execution_mode', 'auth_mode' (may be None)
    5. pipeline_config (triage output): {'execution_modes': {agent_type: str},
                                          'auth_modes': {agent_type: str}}

    Validation raises AuthConfigError with the appropriate error_code.
    """
    # -- execution_mode resolution
    repo_em = (repo_prefs or {}).get("execution_mode")
    job_em = (job_overrides or {}).get("execution_mode")
    agent_em = agent_definition.get("execution_mode")
    pipeline_em = pipeline_config.get("execution_modes", {}).get(agent_type)
    execution_mode = _pick(global_defaults["execution_mode"], repo_em, job_em, agent_em, pipeline_em)
    if execution_mode not in _VALID_EXECUTION_MODES:
        raise AuthConfigError(
            ErrorCode.INVALID_CONFIG,
            f"unknown execution_mode: {execution_mode!r} (expected sdk|cli)",
        )
    if execution_mode not in _PHASE1_ALLOWED_EXECUTION_MODES:
        raise AuthConfigError(
            ErrorCode.INVALID_CONFIG,
            f"execution_mode={execution_mode!r} is not yet supported (Phase 2)",
        )

    # -- auth_mode resolution
    repo_am = (repo_prefs or {}).get("auth_mode")
    job_am = (job_overrides or {}).get("auth_mode")
    agent_am = agent_definition.get("auth_mode")
    pipeline_am = pipeline_config.get("auth_modes", {}).get(agent_type)
    auth_mode = _pick(global_defaults["auth_mode"], repo_am, job_am, agent_am, pipeline_am)
    if auth_mode not in _VALID_AUTH_MODES:
        raise AuthConfigError(
            ErrorCode.INVALID_CONFIG,
            f"unknown auth_mode: {auth_mode!r} (expected api_key|oauth)",
        )

    # -- credential availability
    api_key = credentials.get("api_key", "")
    oauth_token = credentials.get("oauth_token", "")
    if auth_mode == "api_key" and not api_key:
        raise AuthConfigError(
            ErrorCode.MISSING_API_KEY,
            "auth_mode=api_key but ANTHROPIC_API_KEY is empty",
        )
    if auth_mode == "oauth" and not oauth_token:
        raise AuthConfigError(
            ErrorCode.MISSING_OAUTH_TOKEN,
            "auth_mode=oauth but no CLAUDE_CODE_OAUTH_TOKEN available",
        )

    # -- remaining fields from existing resolver logic (reuse ResolvedAgentConfig)
    resolved = resolve_agent_config(
        agent_type=agent_type,
        agent_definition=agent_definition,
        template_config=None,
        pipeline_config=pipeline_config,
    )

    # -- system_prompt, skills, mcp_servers: thread through (plumbing gap closure)
    system_prompt = agent_definition.get("system_prompt", "") or ""
    # pipeline (triage output) can override via pipeline_config.system_prompts[agent_type]
    pipeline_system_prompt = pipeline_config.get("system_prompts", {}).get(agent_type)
    if pipeline_system_prompt:
        system_prompt = pipeline_system_prompt
    skills = resolved.skills or []
    mcp_servers = agent_definition.get("mcp_servers", {}) or {}
    # pipeline override for mcp_servers too
    pipeline_mcp = pipeline_config.get("mcp_servers", {}).get(agent_type)
    if pipeline_mcp is not None:
        mcp_servers = pipeline_mcp

    return AgentInvocation(
        agent_type=agent_type,
        prompt=prompt,
        system_prompt=system_prompt,
        system_context=system_context,
        model=resolved.model or "claude-sonnet-4-6",
        tools=list(resolved.tools),
        skills=list(skills),
        mcp_servers=dict(mcp_servers),
        max_turns=max_turns,
        timeout_seconds=timeout_seconds,
        execution_mode=execution_mode,
        auth_mode=auth_mode,
        safety_policy=default_policy_for_agent(agent_type),
        channel_config=None,  # Phase 3
    )
