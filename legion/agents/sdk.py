"""Claude Agent SDK wrapper — runs LLM calls via Claude CLI subprocess.

Uses stored OAuth credentials from ~/.claude/.credentials.json.
No API key required — authentication is handled by the CLI.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from typing import Any

import structlog
from pydantic import BaseModel

logger = structlog.get_logger(__name__)


class AgentResult(BaseModel):
    """Standardized result from an Agent SDK call."""

    success: bool
    raw_output: str | None = None
    error: str | None = None
    agent_name: str = ""
    execution_time_ms: int = 0
    usage: dict[str, int] | None = None


def _find_cli() -> str | None:
    """Find the claude CLI binary."""
    path = shutil.which("claude")
    if path:
        return path
    # Common locations
    from pathlib import Path
    for candidate in [
        str(Path.home() / ".local" / "bin" / "claude"),
        "/usr/local/bin/claude",
    ]:
        import os

        if os.path.isfile(candidate):
            return candidate
    return None


async def run_agent(
    prompt: str,
    *,
    agent_name: str = "agent",
    model: str = "claude-sonnet-4-6",
    tools: list[str] | None = None,
    system_prompt: str | None = None,
    timeout_seconds: int = 120,
) -> AgentResult:
    """Execute an LLM call via the Claude Agent SDK.

    Spawns a Claude CLI subprocess that authenticates using stored OAuth
    credentials. No API key needed.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    start_time = time.time()

    try:
        cli_path = _find_cli()
        stderr_lines: list[str] = []

        options = ClaudeAgentOptions(
            model=model,
            allowed_tools=tools if tools else [],
            permission_mode="bypassPermissions",
            cwd="/tmp",
            system_prompt=system_prompt,
            stderr=lambda line: stderr_lines.append(line),
            # Strip inherited auth tokens so the CLI uses its own stored credentials
            env={"ANTHROPIC_AUTH_TOKEN": "", "ANTHROPIC_API_KEY": ""},
        )
        if cli_path:
            options.cli_path = cli_path

        output_text = ""
        usage_data: dict[str, int] | None = None

        async def execute() -> None:
            nonlocal output_text, usage_data
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            output_text += block.text
                elif isinstance(message, ResultMessage):
                    if message.result and not output_text:
                        output_text = message.result
                    msg_usage = getattr(message, "usage", None)
                    if msg_usage and isinstance(msg_usage, dict):
                        usage_data = {
                            "input_tokens": msg_usage.get("input_tokens", 0),
                            "output_tokens": msg_usage.get("output_tokens", 0),
                        }

        await asyncio.wait_for(execute(), timeout=timeout_seconds)

        elapsed_ms = int((time.time() - start_time) * 1000)

        if not output_text:
            return AgentResult(
                success=False,
                error="Agent returned no output",
                agent_name=agent_name,
                execution_time_ms=elapsed_ms,
                usage=usage_data,
            )

        return AgentResult(
            success=True,
            raw_output=output_text,
            agent_name=agent_name,
            execution_time_ms=elapsed_ms,
            usage=usage_data,
        )

    except asyncio.TimeoutError:
        elapsed_ms = int((time.time() - start_time) * 1000)
        return AgentResult(
            success=False,
            error=f"Timeout after {timeout_seconds}s",
            agent_name=agent_name,
            execution_time_ms=elapsed_ms,
        )

    except Exception as exc:
        elapsed_ms = int((time.time() - start_time) * 1000)
        error_detail = str(exc)
        if stderr_lines:
            error_detail += f"\nStderr: {''.join(stderr_lines[-10:])}"
        logger.error("agent_sdk_failed", agent_name=agent_name, error=error_detail)
        return AgentResult(
            success=False,
            error=error_detail,
            agent_name=agent_name,
            execution_time_ms=elapsed_ms,
        )
