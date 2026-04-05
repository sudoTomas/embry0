"""Orchestrator-side agent runner — bridges LangGraph and sandbox.

Handles: docker exec into sandbox, stdout stream parsing, event
forwarding to LangGraph, and AgentOutput construction.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from legion.execution.docker_client import DockerClient
from legion.execution.events import parse_event
from legion.execution.sandbox_manager import SandboxManager

logger = structlog.get_logger(__name__)


@dataclass
class AgentOutput:
    """Result of an agent execution inside the sandbox."""

    agent_type: str
    is_error: bool = False
    error_message: str = ""
    output: str = ""
    cost_usd: float = 0.0
    duration_ms: int = 0
    tools_called: dict[str, int] = field(default_factory=dict)


class AgentRunner:
    """Runs agents inside sandbox containers via docker exec."""

    def __init__(self, sandbox_manager: SandboxManager, docker: DockerClient) -> None:
        self._sandbox = sandbox_manager
        self._docker = docker

    async def run(
        self,
        container: str,
        config: dict[str, Any],
        network: str | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentOutput:
        """Execute an agent inside the sandbox and return the result."""
        if network:
            await self._sandbox.connect_network(container, network)

        try:
            config_json = json.dumps(config)
            proc = await self._docker.stream_exec(
                container=container,
                command=["python", "-m", "legion.sandbox.runner", "--config", config_json],
                workdir="/workspace",
            )

            timeout_seconds: float = config.get("timeout_seconds", 300)

            async def _read_and_wait() -> list[str]:
                collected: list[str] = []
                if proc.stdout is not None:
                    async for raw_line in proc.stdout:
                        line = raw_line.decode("utf-8", errors="replace")
                        collected.append(line)
                await proc.wait()
                return collected

            try:
                lines = await asyncio.wait_for(_read_and_wait(), timeout=timeout_seconds)
            except TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                return AgentOutput(
                    agent_type=config.get("agent_type", "unknown"),
                    is_error=True,
                    error_message=f"Agent timed out after {timeout_seconds}s",
                )

            logger.info("agent_stdout_captured", agent_type=config.get("agent_type"), line_count=len(lines), exit_code=proc.returncode)
            if lines:
                logger.debug("agent_stdout_first_line", line=lines[0][:200])
                logger.debug("agent_stdout_last_line", line=lines[-1][:200])

            result = self._parse_stdout_events(lines, on_event=on_event or (lambda _: None))

            if result:
                return AgentOutput(
                    agent_type=result.get("agent_type", "unknown"),
                    is_error=result.get("is_error", False),
                    error_message=result.get("error_message", ""),
                    output=result.get("output", ""),
                    cost_usd=result.get("cost_usd", 0.0),
                    duration_ms=result.get("duration_ms", 0),
                    tools_called=result.get("tools_called", {}),
                )

            return AgentOutput(
                agent_type=config.get("agent_type", "unknown"),
                is_error=True,
                error_message="No final result received from sandbox",
            )

        finally:
            if network:
                try:
                    await self._sandbox.disconnect_network(container, network)
                except RuntimeError:
                    logger.warning("network_disconnect_failed", container=container, network=network)

    def _parse_stdout_events(
        self,
        lines: list[str],
        on_event: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any] | None:
        """Parse JSON lines from stdout. Forward events, return final result."""
        final_result: dict[str, Any] | None = None

        for line in lines:
            event = parse_event(line)
            if event is None:
                continue

            if event.get("type") == "final_result":
                final_result = event
            else:
                on_event(event)

        return final_result
