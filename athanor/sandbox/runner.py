"""Sandbox agent runner — entrypoint for `python -m athanor.sandbox.runner`.

Called by the orchestrator via `docker exec`. Parses the --config JSON into
an AgentInvocation and delegates to SdkAgentExecutor (Phase 1). Emits Athanor
events to stdout as JSON-per-line.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import Any

import structlog

from athanor.agents.executor_factory import select_executor
from athanor.agents.invocation import AgentInvocation
from athanor.safety.policy import default_policy_for_agent

logger = structlog.get_logger(__name__)


def _emit(event: dict[str, Any]) -> None:
    """Write a JSON event to stdout and flush."""
    event.setdefault("timestamp", datetime.now(UTC).isoformat())
    sys.stdout.write(json.dumps(event, default=str) + "\n")
    sys.stdout.flush()


def _invocation_from_config(cfg: dict[str, Any]) -> AgentInvocation:
    """Build an AgentInvocation from the --config JSON the orchestrator passes.

    The orchestrator fully resolves the invocation already; this function
    only unpacks it. Backward-compat: a few older fields (`prompt`,
    `system_context`) are still accepted in the flat shape.
    """
    agent_type = cfg.get("agent_type", "agent")
    return AgentInvocation(
        agent_type=agent_type,
        prompt=cfg["prompt"],
        system_prompt=cfg.get("system_prompt", ""),
        system_context=cfg.get("system_context", ""),
        model=cfg.get("model", "claude-sonnet-4-6"),
        tools=cfg.get("tools", []),
        skills=cfg.get("skills", []),
        mcp_servers=cfg.get("mcp_servers", {}),
        max_turns=cfg.get("max_turns", 40),
        timeout_seconds=cfg.get("timeout_seconds", 300),
        execution_mode=cfg.get("execution_mode", "sdk"),
        auth_mode=cfg.get("auth_mode", "oauth"),
        safety_policy=default_policy_for_agent(agent_type),
        channel_config=None,
    )


async def run_agent(config: dict[str, Any]) -> dict[str, Any]:
    invocation = _invocation_from_config(config)
    executor = select_executor(invocation)
    # Inject a writer that serializes events to stdout (Athanor's wire format).
    test_cfg = {"configurable": {}, "_test_writer": _emit}
    result = await executor.run(invocation, test_cfg)
    return {
        "agent_type": result.agent_type,
        "is_error": result.is_error,
        "error_message": result.error_message,
        "output": result.output,
        "cost_usd": result.cost_usd,
        "duration_ms": result.duration_ms,
        "tools_called": result.tools_called,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Athanor sandbox agent runner")
    parser.add_argument("--config", required=True, help="JSON agent configuration")
    args = parser.parse_args()

    config = json.loads(args.config)
    result = asyncio.run(run_agent(config))

    sys.stdout.write(json.dumps({"type": "final_result", **result}, default=str) + "\n")
    sys.stdout.flush()
    sys.exit(1 if result.get("is_error") else 0)


if __name__ == "__main__":
    main()
