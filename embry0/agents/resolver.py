"""Agent config resolution — merges definition, template, and runtime overrides."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import structlog

from embry0.agents.invocation import AgentInvocation
from embry0.agents.skills import effective_skills
from embry0.execution.auth_provider import AuthConfigError
from embry0.safety.error_codes import ErrorCode
from embry0.safety.policy import default_policy_for_agent

logger = structlog.get_logger(__name__)

# RAV-1297: when a pinned model was disabled AFTER the agent row/template
# was saved, the run falls back to a tier default instead of failing —
# heavy for the developer (mirrors the deployed matrix), medium otherwise.
# Tier models themselves can never be disabled (API interlock).
_FALLBACK_TIER_FOR_AGENT = {"developer": "model_heavy"}


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
    *,
    default_model: str = "",
    job_agent_models: Mapping[str, str] | None = None,
) -> ResolvedAgentConfig:
    """Merge chain: agent_definition <- template overrides <- runtime overrides.

    For tools: later levels replace earlier. For skills: each level is a
    delta (add/remove) over the previous, a bare list meaning replace
    (``embry0.agents.skills.effective_skills``).
    system_prompt comes only from agent_definition.

    The model has two more levels (#465). Later wins:

    1. ``agent_definition["model"]``
    2. ``default_model`` — the deployment-wide default, when set
    3. ``template_config["agent_models"]`` — the pipeline template step
    4. ``pipeline_config["agent_models"]`` — triage's picks, **only when no
       default is set**: a default is an operator instruction, and triage is
       an LLM that must not outvote it
    5. ``job_agent_models`` — the job's explicit models (job create, a model
       label on the ticket)
    """
    model = agent_definition.get("model", "")
    tools = _ensure_list(agent_definition.get("tools", []))
    skills = _ensure_list(agent_definition.get("skills", []))
    system_prompt = agent_definition.get("system_prompt", "")

    if default_model:
        model = default_model

    # Template overrides
    if template_config:
        if agent_type in template_config.get("agent_models", {}):
            model = template_config["agent_models"][agent_type]
        if agent_type in template_config.get("agent_tools", {}):
            tools = template_config["agent_tools"][agent_type]

    # Runtime (triage) overrides
    if pipeline_config:
        triage_models = pipeline_config.get("agent_models") or {}
        if agent_type in triage_models:
            if default_model:
                logger.info(
                    "triage_model_ignored_default_model_set",
                    agent_type=agent_type,
                    triage_model=triage_models[agent_type],
                    default_model=default_model,
                )
            else:
                model = triage_models[agent_type]
        if agent_type in pipeline_config.get("agent_tools", {}):
            tools = pipeline_config["agent_tools"][agent_type]

    skills = effective_skills(
        skills,
        ((template_config or {}).get("agent_skills") or {}).get(agent_type),
        ((pipeline_config or {}).get("agent_skills") or {}).get(agent_type),
    )

    # Explicit job-level models — the most specific instruction there is.
    if job_agent_models and agent_type in job_agent_models:
        model = job_agent_models[agent_type]

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
    template_config: dict[str, Any] | None = None,
    model_policy: dict[str, Any] | None = None,
    job_agent_models: Mapping[str, str] | None = None,
) -> AgentInvocation:
    """Resolve a fully-validated AgentInvocation from the five-level chain.

    Precedence (lowest -> highest):
    1. global_defaults: {'execution_mode': str, 'auth_mode': str}
    2. repo_prefs (optional): {'execution_mode': str | None, 'auth_mode': str | None}
    3. job_overrides (optional): same shape
    4. agent_definition: includes 'execution_mode', 'auth_mode' (may be None)
    5. pipeline_config (triage output): {'execution_modes': {agent_type: str},
                                          'auth_modes': {agent_type: str}}

    The model resolves separately — see ``resolve_agent_config`` for its
    order. ``model_policy`` carries the deployment's ``default_model`` and
    the enablement set; ``job_agent_models`` is the job's explicit per-agent
    models, kept apart from ``pipeline_config`` so they can be told from the
    ones triage chose.

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

    # -- remaining fields from existing resolver logic (reuse ResolvedAgentConfig)
    resolved = resolve_agent_config(
        agent_type=agent_type,
        agent_definition=agent_definition,
        # RAV-602: per-step template overrides (route_plan step config) sit
        # between the DB definition and triage's runtime pipeline_config.
        template_config=template_config,
        pipeline_config=pipeline_config,
        default_model=str((model_policy or {}).get("default_model") or ""),
        job_agent_models=job_agent_models,
    )

    from embry0.agents.providers import provider_for_model, unpriced_models, unpriced_models_message

    final_model = resolved.model or "claude-sonnet-5"

    # RAV-1297 enablement: model_policy = {"default_model": ...,
    # "disabled_models": [...],
    # "tier_models": {"model_heavy": ..., "model_medium": ..., "model_light": ...}}
    # (loaded best-effort from provider_config by run_agent_node). A disabled
    # final model falls back to the agent's tier default with a warning; if
    # no runnable fallback exists, the pinned model runs anyway (fail-open —
    # an enablement toggle must never kill jobs).
    disabled = set((model_policy or {}).get("disabled_models") or [])
    if final_model in disabled:
        tier_models = (model_policy or {}).get("tier_models") or {}
        tier = _FALLBACK_TIER_FOR_AGENT.get(agent_type, "model_medium")
        fallback = tier_models.get(tier) or ""
        if fallback and fallback not in disabled:
            logger.warning(
                "agent_model_disabled_fallback",
                agent_type=agent_type,
                disabled_model=final_model,
                fallback_model=fallback,
                tier=tier,
            )
            final_model = fallback
        else:
            logger.warning(
                "agent_model_disabled_no_fallback",
                agent_type=agent_type,
                disabled_model=final_model,
            )

    # #219: a provider-routed model with no price entry would run unmetered —
    # the provider reports no cost, so the price table is the only accounting.
    # Refused at configuration time; this catches what reached the run anyway
    # (an env tier model, triage's pick, a row saved before the check).
    unpriced = unpriced_models([final_model])
    if unpriced:
        raise AuthConfigError(ErrorCode.INVALID_CONFIG, unpriced_models_message(unpriced))

    provider = provider_for_model(final_model)

    # The caller's budget is sized for Claude; a provider whose models need more turns and
    # time for the same work scales it. Done here because this is where the model is final.
    if provider is not None:
        max_turns = math.ceil(max_turns * provider.turn_budget_factor)
        timeout_seconds = math.ceil(timeout_seconds * provider.timeout_factor)

    # -- credential availability (Anthropic-backed agents only, RAV-1240):
    # provider-routed agents never use these credentials — the executor
    # overlays provider auth and strips the Anthropic vars, and missing
    # provider credentials fail closed in-executor before any spend.
    if provider is None:
        api_key = credentials.get("api_key", "")
        oauth_token = credentials.get("oauth_token", "")
        # Non-secret. run_agent_node copies it from configurable llm_credential_kinds.
        kind = credentials.get("anthropic_kind") or ""
        if auth_mode == "api_key":
            if kind and kind != "llm_api_key":
                raise AuthConfigError(
                    ErrorCode.MISSING_API_KEY,
                    f"auth_mode=api_key but the resolved account is {kind}",
                )
            # A resolved llm_api_key account is the key (registry or env, #572);
            # the env string is only consulted when no account was resolved.
            if not api_key and kind != "llm_api_key":
                raise AuthConfigError(
                    ErrorCode.MISSING_API_KEY,
                    "auth_mode=api_key but ANTHROPIC_API_KEY is empty",
                )
        if auth_mode == "oauth":
            if kind and kind != "llm_oauth":
                raise AuthConfigError(
                    ErrorCode.MISSING_OAUTH_TOKEN,
                    f"auth_mode=oauth but the resolved account is {kind}",
                )
            if not oauth_token and kind != "llm_oauth":
                raise AuthConfigError(
                    ErrorCode.MISSING_OAUTH_TOKEN,
                    "auth_mode=oauth but no CLAUDE_CODE_OAUTH_TOKEN available",
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
        model=final_model,
        tools=list(resolved.tools),
        skills=list(skills),
        mcp_servers=dict(mcp_servers),
        max_turns=max_turns,
        timeout_seconds=timeout_seconds,
        execution_mode=execution_mode,
        auth_mode=auth_mode,
        safety_policy=default_policy_for_agent(agent_type),
        channel_config=None,  # Phase 3
        provider=provider.name if provider else None,
    )
