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

from athanor.execution.docker_client import DockerClient
from athanor.execution.events import parse_event
from athanor.execution.sandbox_manager import SandboxManager

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
                command=["python", "-m", "athanor.sandbox.runner", "--config", config_json],
                workdir="/workspace",
            )

            timeout_seconds: float = config.get("timeout_seconds", 300)

            async def _read_and_wait() -> AgentOutput:
                final_result: dict[str, Any] | None = None
                event_callback = on_event or (lambda _: None)
                line_count = 0

                if proc.stdout is not None:
                    async for raw_line in proc.stdout:
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line:
                            continue
                        line_count += 1
                        event = parse_event(line)
                        if event is None:
                            continue
                        if event.get("type") == "final_result":
                            final_result = event
                        else:
                            event_callback(event)

                await proc.wait()

                logger.info(
                    "agent_stdout_captured",
                    agent_type=config.get("agent_type"),
                    line_count=line_count,
                    exit_code=proc.returncode,
                )

                if final_result:
                    return AgentOutput(
                        agent_type=final_result.get("agent_type", "unknown"),
                        is_error=final_result.get("is_error", False),
                        error_message=final_result.get("error_message", ""),
                        output=final_result.get("output", ""),
                        cost_usd=final_result.get("cost_usd", 0.0),
                        duration_ms=final_result.get("duration_ms", 0),
                        tools_called=final_result.get("tools_called", {}),
                    )

                return AgentOutput(
                    agent_type=config.get("agent_type", "unknown"),
                    is_error=True,
                    error_message="No final result received from sandbox",
                )

            try:
                result = await asyncio.wait_for(_read_and_wait(), timeout=timeout_seconds)
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

            return result

        finally:
            if network:
                try:
                    await self._sandbox.disconnect_network(container, network)
                except RuntimeError:
                    logger.warning("network_disconnect_failed", container=container, network=network)
