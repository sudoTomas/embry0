"""Orchestrator-side SDK fallback — used when no sandbox is available.

Today's only caller is the legacy triage_node fallback path. This module
is a thin wrapper around SdkAgentExecutor that adapts the legacy function
signature to the new AgentInvocation + executor pipeline.
"""

from __future__ import annotations

from pydantic import BaseModel

from athanor.agents.executor_factory import select_executor
from athanor.agents.invocation import AgentInvocation
from athanor.safety.policy import default_policy_for_agent


class AgentResult(BaseModel):
    success: bool
    raw_output: str | None = None
    error: str | None = None
    agent_name: str = ""
    execution_time_ms: int = 0
    usage: dict[str, int] | None = None


async def run_agent(
    prompt: str,
    *,
    agent_name: str = "agent",
    model: str = "claude-sonnet-4-6",
    tools: list[str] | None = None,
    system_prompt: str | None = None,
    timeout_seconds: int = 120,
) -> AgentResult:
    invocation = AgentInvocation(
        agent_type=agent_name,
        prompt=prompt,
        system_prompt=system_prompt or "",
        system_context="",
        model=model,
        tools=tools or [],
        skills=[],
        mcp_servers={},
        max_turns=40,
        timeout_seconds=timeout_seconds,
        execution_mode="sdk",
        auth_mode="oauth",
        safety_policy=default_policy_for_agent(agent_name),
        channel_config=None,
    )

    executor = select_executor(invocation)
    out = await executor.run(
        invocation,
        config={"configurable": {}, "_test_writer": lambda _e: None},
    )

    return AgentResult(
        success=not out.is_error,
        raw_output=out.output if not out.is_error else None,
        error=out.error_message or None,
        agent_name=agent_name,
        execution_time_ms=out.duration_ms,
        usage=None,
    )
