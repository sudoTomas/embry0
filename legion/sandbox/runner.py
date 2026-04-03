"""Sandbox agent runner — the entrypoint executed via `docker exec`.

Receives agent configuration, calls Agent SDK query(), enforces
safety via hooks, and emits structured events to stdout.
"""

import argparse
import asyncio
import json
import sys
import time
from typing import Any

import structlog

from legion.sandbox.events import EventType, emit_event

logger = structlog.get_logger(__name__)


async def run_agent(config: dict[str, Any]) -> dict[str, Any]:
    """Execute an agent using the Claude Agent SDK."""
    agent_type = config.get("agent_type", "agent")
    emit_event(EventType.AGENT_STARTED, agent=agent_type)

    start_time = time.time()
    tools_called: dict[str, int] = {}
    output_text = ""
    cost_usd = 0.0
    is_error = False
    error_message = ""

    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            query,
        )

        options = ClaudeAgentOptions(
            model=config.get("model", "claude-sonnet-4-6"),
            allowed_tools=config.get("tools", []),
            permission_mode="bypassPermissions",
            max_turns=config.get("max_turns", 40),
            cwd="/workspace",
        )

        async def execute() -> None:
            nonlocal output_text, cost_usd
            async for message in query(prompt=config["prompt"], options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            output_text += block.text
                elif isinstance(message, ResultMessage):
                    if message.result and not output_text:
                        output_text = message.result
                    cost_usd = getattr(message, "total_cost_usd", 0.0) or 0.0

        timeout = config.get("timeout_seconds", 300)
        await asyncio.wait_for(execute(), timeout=timeout)

    except TimeoutError:
        is_error = True
        error_message = f"Agent timed out after {config.get('timeout_seconds', 300)}s"
        emit_event(EventType.ERROR, error=error_message)
    except Exception as exc:
        is_error = True
        error_message = str(exc)
        emit_event(EventType.ERROR, error=error_message)

    elapsed_ms = int((time.time() - start_time) * 1000)

    result = {
        "agent_type": agent_type,
        "is_error": is_error,
        "error_message": error_message,
        "output": output_text[:5000],
        "cost_usd": cost_usd,
        "duration_ms": elapsed_ms,
        "tools_called": tools_called,
    }

    emit_event(
        EventType.AGENT_COMPLETED,
        result=result,
        cost_usd=cost_usd,
        duration_ms=elapsed_ms,
        tools_called=tools_called,
    )
    return result


def main() -> None:
    """CLI entrypoint: python -m legion.sandbox.runner --config '{...}'"""
    parser = argparse.ArgumentParser(description="Legion sandbox agent runner")
    parser.add_argument("--config", required=True, help="JSON agent configuration")
    args = parser.parse_args()

    config = json.loads(args.config)
    result = asyncio.run(run_agent(config))

    sys.stdout.write(json.dumps({"type": "final_result", **result}, default=str) + "\n")
    sys.stdout.flush()

    sys.exit(1 if result.get("is_error") else 0)


if __name__ == "__main__":
    main()
