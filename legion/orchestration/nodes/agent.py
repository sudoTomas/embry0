"""Agent execution node — bridges LangGraph state to sandbox AgentRunner."""

from typing import Any

import structlog

from legion.agents.resolver import resolve_agent_config
from legion.execution.agent_runner import AgentOutput, AgentRunner
from legion.orchestration.state import AgentOutputEntry

logger = structlog.get_logger(__name__)


async def run_agent_node(
    state: dict[str, Any],
    agent_runner: AgentRunner,
    agent_type: str,
    prompt: str,
    model: str = "claude-sonnet-4-6",
    tools: list[str] | None = None,
    max_turns: int = 40,
    timeout_seconds: int = 300,
    network: str | None = None,
    on_event: Any | None = None,
    agent_definition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # If an agent definition is provided, resolve config through the override chain
    if agent_definition is not None:
        pipeline_config = state.get("pipeline_config", {})
        template_config = pipeline_config.get("pipeline_config") if isinstance(pipeline_config, dict) else None
        resolved = resolve_agent_config(
            agent_type=agent_type,
            agent_definition=agent_definition,
            template_config=template_config,
            pipeline_config=template_config,
        )
        model = resolved.model or model
        tools = resolved.tools if resolved.tools else tools

    container = state.get("sandbox_container_id", "")
    config = {
        "agent_type": agent_type,
        "prompt": prompt,
        "model": model,
        "tools": tools or [],
        "max_turns": max_turns,
        "timeout_seconds": timeout_seconds,
    }

    result: AgentOutput = await agent_runner.run(
        container=container, config=config, network=network, on_event=on_event,
    )

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
        "current_stage": f"{agent_type}_complete",
    }

    if result.is_error:
        updates["errors"] = [f"{agent_type}: {result.error_message}"]

    logger.info("agent_node_complete", agent_type=agent_type, is_error=result.is_error, cost_usd=result.cost_usd)
    return updates
