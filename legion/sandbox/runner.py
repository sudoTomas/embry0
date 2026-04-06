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
from legion.sandbox.safety import check_tool_safety

logger = structlog.get_logger(__name__)


def _summarize_tool_input(tool_name: str, tool_input: dict) -> str:
    """Create a short summary of tool input for display."""
    if not isinstance(tool_input, dict):
        return str(tool_input)[:200]
    if tool_name in ("Read", "Glob", "Grep"):
        return tool_input.get("file_path", "") or tool_input.get("path", "") or tool_input.get("pattern", "")
    if tool_name in ("Write", "Edit"):
        return tool_input.get("file_path", "")
    if tool_name == "Bash":
        return tool_input.get("command", "")[:200]
    return str(tool_input)[:200]


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

    # Prepend system context to prompt if provided (I2).
    prompt = config["prompt"]
    system_context = config.get("system_context", "")
    if system_context:
        prompt = f"{system_context}\n\n{prompt}"

    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            ToolUseBlock,
            query,
        )

        try:
            from claude_agent_sdk import ThinkingBlock
        except ImportError:
            ThinkingBlock = None
        try:
            from claude_agent_sdk import ToolResultBlock
        except ImportError:
            ToolResultBlock = None

        async def safety_hook(hook_input: Any) -> dict[str, Any]:
            """PreToolUse hook: enforce safety rules and track tool calls (C1, I1)."""
            tool_name = getattr(hook_input, "tool_name", None) or hook_input.get("tool_name", "")
            tool_input = getattr(hook_input, "tool_input", None) or hook_input.get("tool_input", {})

            # Track tool call counts (I1).
            tools_called[tool_name] = tools_called.get(tool_name, 0) + 1

            # Enforce safety rules (C1).
            denial = check_tool_safety(tool_name, tool_input)
            if denial:
                emit_event(EventType.ERROR, error=denial["reason"])
                return denial

            return {"decision": "allow"}

        # NOTE: The hooks parameter requires Agent SDK support for the
        # PreToolUse hook mechanism. If the installed SDK version does not
        # support hooks, safety enforcement via this path will be silently
        # unavailable and tools_called will not be populated via hooks.
        # This is a known limitation until the SDK hook API is confirmed stable.
        options = ClaudeAgentOptions(
            model=config.get("model", "claude-sonnet-4-6"),
            allowed_tools=config.get("tools", []),
            permission_mode="bypassPermissions",
            max_turns=config.get("max_turns", 40),
            cwd="/workspace",
            # Strip inherited API key env vars so the CLI uses OAuth credentials
            # from ~/.claude/.credentials.json instead.
            env={"ANTHROPIC_AUTH_TOKEN": "", "ANTHROPIC_API_KEY": ""},
            hooks={
                "PreToolUse": [{"matcher": None, "hooks": [safety_hook]}],
            },
        )

        async def execute() -> None:
            nonlocal output_text, cost_usd
            turn_number = 0

            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    turn_number += 1
                    emit_event(
                        EventType.TURN_START,
                        turn_number=turn_number,
                        model=getattr(message, "model", ""),
                        node=agent_type,
                    )

                    for block in message.content:
                        if isinstance(block, TextBlock):
                            output_text += block.text
                            emit_event(
                                EventType.TEXT,
                                text=block.text[:2000],
                                message_id=getattr(message, "uuid", ""),
                                node=agent_type,
                            )
                        elif isinstance(block, ToolUseBlock):
                            tool_name = getattr(block, "name", "") or getattr(block, "tool_name", "")
                            if tool_name:
                                tools_called[tool_name] = tools_called.get(tool_name, 0) + 1
                            emit_event(
                                EventType.TOOL_CALL,
                                tool_name=tool_name,
                                tool_id=getattr(block, "id", ""),
                                input=_summarize_tool_input(tool_name, getattr(block, "input", {})),
                                node=agent_type,
                            )
                        elif ThinkingBlock is not None and isinstance(block, ThinkingBlock):
                            emit_event(
                                EventType.THINKING,
                                text=block.thinking[:3000],
                                node=agent_type,
                            )
                        elif ToolResultBlock is not None and isinstance(block, ToolResultBlock):
                            emit_event(
                                EventType.TOOL_RESULT,
                                tool_use_id=getattr(block, "tool_use_id", ""),
                                content=str(getattr(block, "content", ""))[:1000],
                                is_error=getattr(block, "is_error", False),
                                node=agent_type,
                            )

                elif isinstance(message, ResultMessage):
                    if message.result and not output_text:
                        output_text = message.result
                    cost_usd = getattr(message, "total_cost_usd", 0.0) or 0.0
                    usage = getattr(message, "usage", {}) or {}
                    emit_event(
                        EventType.COST_UPDATE,
                        cost_usd=cost_usd,
                        duration_ms=getattr(message, "duration_ms", 0),
                        num_turns=getattr(message, "num_turns", 0),
                        tokens_in=usage.get("input_tokens", 0),
                        tokens_out=usage.get("output_tokens", 0),
                        node=agent_type,
                    )

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
        "output": output_text[-10000:] if len(output_text) > 10000 else output_text,
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
