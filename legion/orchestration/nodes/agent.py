"""Agent execution node — bridges LangGraph state to AgentExecutor."""

from __future__ import annotations

from typing import Any

import structlog

from legion.agents.executor_factory import select_executor
from legion.agents.resolver import resolve_agent_invocation
from legion.execution.agent_runner import AgentOutput
from legion.execution.auth_provider import AuthConfigError
from legion.orchestration.state import AgentOutputEntry

logger = structlog.get_logger(__name__)


def _auth_error_updates(
    agent_type: str,
    exc: AuthConfigError,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Build the failure-state dict for an AuthConfigError from any call site."""
    logger.error(
        "agent_config_error",
        agent_type=agent_type,
        error_code=exc.error_code.value,
        error=str(exc),
    )
    return {
        "agent_outputs": [
            AgentOutputEntry(
                agent_type=agent_type,
                is_error=True,
                error_message=str(exc),
                output="",
                cost_usd=0.0,
                duration_ms=0,
                tools_called={},
            )
        ],
        "errors": [f"{agent_type}: {exc}"],
        "current_stage": f"{agent_type}_failed",
        "total_cost_usd": state.get("total_cost_usd", 0.0),
        "error_code": exc.error_code.value,
    }


async def run_agent_node(
    state: dict[str, Any],
    agent_runner: Any,
    agent_type: str,
    prompt: str,
    model: str = "claude-sonnet-4-6",
    tools: list[str] | None = None,
    max_turns: int = 40,
    timeout_seconds: int = 300,
    network: str | None = None,
    on_event: Any | None = None,
    agent_definition: dict[str, Any] | None = None,
    credentials: dict[str, str] | None = None,
    global_defaults: dict[str, Any] | None = None,
    config: Any | None = None,
) -> dict[str, Any]:
    """Resolve an AgentInvocation and run it via the chosen executor.

    When ``agent_runner`` is provided (production), the fully-resolved
    invocation is serialized and shipped to the sandbox container via
    ``AgentRunner.run``; the in-container ``SdkAgentExecutor`` performs the
    actual Claude call. When ``agent_runner`` is ``None`` (legacy/non-sandboxed
    fallback), the executor runs in-process.

    Returns a LangGraph state update dict. Does NOT return a Command here;
    Command returns live in the workflow nodes (see Task 16).
    """
    # Build a default agent_definition if the caller didn't supply one. We
    # thread the model/tools kwargs through so legacy callers still get the
    # same effective configuration.
    agent_definition = agent_definition or {
        "model": model,
        "tools": tools or [],
        "skills": [],
        "system_prompt": "",
        "mcp_servers": {},
        "execution_mode": None,
        "auth_mode": None,
    }
    # If the caller didn't supply credentials/defaults, fall back to conservative
    # defaults that preserve today's runtime behavior (oauth + empty api_key).
    credentials = credentials or {"api_key": "", "oauth_token": ""}
    global_defaults = global_defaults or {"execution_mode": "sdk", "auth_mode": "oauth"}

    # pipeline_config on state may be a raw PipelineConfig OR a TriageDecision
    # that wraps one under the "pipeline_config" key. Unwrap one level if so.
    pipeline_config = state.get("pipeline_config") or {}
    if isinstance(pipeline_config, dict) and "pipeline_config" in pipeline_config:
        pipeline_config = pipeline_config.get("pipeline_config") or {}

    # Per-job agent_models override (JobCreateRequest.agent_models) — surfaced
    # onto state by IssueExecutor. It wins over template/definition precedence,
    # so fold it into the pipeline_config.agent_models map the resolver reads.
    override_models = state.get("agent_models_override") or {}
    if isinstance(override_models, dict) and agent_type in override_models:
        merged_models = dict(pipeline_config.get("agent_models") or {})
        merged_models[agent_type] = override_models[agent_type]
        pipeline_config = {**pipeline_config, "agent_models": merged_models}

    try:
        invocation = resolve_agent_invocation(
            agent_type=agent_type,
            prompt=prompt,
            system_context=state.get("global_context") or "",
            global_defaults=global_defaults,
            repo_prefs=state.get("repo_preferences"),
            job_overrides={
                "execution_mode": state.get("execution_mode_override"),
                "auth_mode": state.get("auth_mode_override"),
            },
            agent_definition=agent_definition,
            pipeline_config=pipeline_config or {},
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
            credentials=credentials,
        )
    except AuthConfigError as exc:
        return _auth_error_updates(agent_type, exc, state)

    # Build the serialized invocation dict the sandbox runner receives.
    serialized = {
        "agent_type": invocation.agent_type,
        "prompt": invocation.prompt,
        "system_prompt": invocation.system_prompt,
        "system_context": invocation.system_context,
        "model": invocation.model,
        "tools": list(invocation.tools),
        "skills": list(invocation.skills),
        "mcp_servers": dict(invocation.mcp_servers),
        "max_turns": invocation.max_turns,
        "timeout_seconds": invocation.timeout_seconds,
        "execution_mode": invocation.execution_mode,
        "auth_mode": invocation.auth_mode,
    }

    container = state.get("sandbox_container_id", "")

    try:
        if agent_runner is None:
            # No sandbox available (legacy non-sandboxed fallback) — run in-process.
            executor = select_executor(invocation)
            result: AgentOutput = await executor.run(invocation, config)
        else:
            # Production path: docker-exec the sandbox runner with the
            # serialized invocation; the in-container SdkAgentExecutor
            # handles the actual Claude call.
            result = await agent_runner.run(
                container=container,
                config=serialized,
                network=network,
                on_event=on_event,
            )
    except AuthConfigError as exc:
        return _auth_error_updates(agent_type, exc, state)

    output_entry = AgentOutputEntry(
        agent_type=result.agent_type,
        is_error=result.is_error,
        error_message=result.error_message,
        output=result.output,
        cost_usd=result.cost_usd,
        duration_ms=result.duration_ms,
        tools_called=result.tools_called,
    )

    updates: dict[str, Any] = {
        "agent_outputs": [output_entry],
        "total_cost_usd": state.get("total_cost_usd", 0.0) + result.cost_usd,
        "current_stage": f"{agent_type}_complete" if not result.is_error else f"{agent_type}_failed",
    }
    if result.is_error:
        updates["errors"] = [f"{agent_type}: {result.error_message}"]

    logger.info(
        "agent_node_complete",
        agent_type=agent_type,
        is_error=result.is_error,
        cost_usd=result.cost_usd,
        execution_mode=invocation.execution_mode,
        auth_mode=invocation.auth_mode,
    )
    return updates
