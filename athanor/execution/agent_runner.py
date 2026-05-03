"""Orchestrator-side agent runner — bridges LangGraph and sandbox.

Handles: docker exec into sandbox, stdout stream parsing, event
forwarding to LangGraph, and AgentOutput construction.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from athanor.execution.docker_client import DockerClient
from athanor.execution.events import parse_event
from athanor.execution.sandbox_manager import SandboxManager

if TYPE_CHECKING:
    from athanor.agents.session import AgentSession

logger = structlog.get_logger(__name__)


# Sandbox-side path where the orchestrator stages the resume blob for
# anthropic_api mode. Tmpfs-backed and disposed with the sandbox.
RESUME_BLOB_SANDBOX_PATH = "/tmp/.athanor-resume-blob"


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
    # Post-run conversation state (populated by the runner so Plan C Task 6
    # can persist via AgentSessionsRepository). All optional — omitted by
    # legacy code paths and by error returns.
    messages: list[dict[str, Any]] | None = None
    session_id: str | None = None
    session_blob_path: str | None = None


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
        *,
        resume_session: AgentSession | None = None,
    ) -> AgentOutput:
        """Execute an agent inside the sandbox and return the result.

        ``resume_session`` (Plan C Task 5) carries prior conversation state.
        When non-None it is staged into the sandbox via ``docker cp`` and
        the runner is told via CLI args (``--session-blob`` / ``--session-id``)
        where to find it. ``None`` is the legacy path — no copy, no extra args.
        """
        if network:
            await self._sandbox.connect_network(container, network)

        try:
            extra_args = await self._stage_resume_session(container, resume_session)

            config_json = json.dumps(config)
            command = [
                "python",
                "-m",
                "athanor.sandbox.runner",
                "--config",
                config_json,
                *extra_args,
            ]
            proc = await self._docker.stream_exec(
                container=container,
                command=command,
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
                        messages=final_result.get("messages"),
                        session_id=final_result.get("session_id"),
                        session_blob_path=final_result.get("session_blob_path"),
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

    async def _stage_resume_session(
        self,
        container: str,
        resume_session: AgentSession | None,
    ) -> list[str]:
        """Copy the prior session into ``container`` and return runner CLI args.

        Returns an empty list when ``resume_session`` is None (legacy path).
        For ``anthropic_api`` mode: dumps ``resume_session.messages`` to a host
        tempfile, ``docker cp``s it to ``RESUME_BLOB_SANDBOX_PATH`` inside the
        sandbox, and returns ``["--session-blob", <path>]``. For ``claude_max``
        mode: streams ``resume_session.session_blob`` bytes into the CLI's
        canonical session-file path and returns ``["--session-id", <id>]``.
        Modes with insufficient data (e.g. session_blob is None) become no-ops.
        """
        if resume_session is None:
            return []

        if resume_session.mode == "anthropic_api":
            if resume_session.messages is None:
                return []
            fd, tmp_path = tempfile.mkstemp(prefix="athanor-resume-", suffix=".json")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(resume_session.messages, f)
                await self._docker.copy_into(container, tmp_path, RESUME_BLOB_SANDBOX_PATH)
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            return ["--session-blob", RESUME_BLOB_SANDBOX_PATH]

        if resume_session.mode == "claude_max":
            if not resume_session.session_blob or not resume_session.session_id:
                return []
            sandbox_path = f"/home/agent/.claude/sessions/{resume_session.session_id}.jsonl"
            await self._docker.copy_bytes_into(container, resume_session.session_blob, sandbox_path)
            return ["--session-id", resume_session.session_id]

        # Defensive: future modes should be wired explicitly.
        logger.warning("agent_runner_unknown_session_mode", mode=resume_session.mode)
        return []
