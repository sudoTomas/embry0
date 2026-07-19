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
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from embry0.agents.claude_cli_session import canonical_session_path_for
from embry0.execution.docker_client import DockerClient
from embry0.execution.events import parse_event

if TYPE_CHECKING:
    # SandboxManager is only used as an __init__ type annotation. Importing
    # it eagerly would pull in embry0.execution.image_registry and the
    # whole image-build chain, which is not needed inside the sandbox where
    # this module is loaded only for the AgentOutput dataclass.
    from embry0.agents.session import AgentSession
    from embry0.execution.sandbox_manager import SandboxManager

logger = structlog.get_logger(__name__)


# Sandbox-side path where the orchestrator stages the resume blob for
# anthropic_api mode. Tmpfs-backed and disposed with the sandbox.
RESUME_BLOB_SANDBOX_PATH = "/tmp/.embry0-resume-blob"

# Sandbox-side cwd for agent processes. Pinned to /workspace via the
# DockerClient `-w` flag — used by canonical_session_path_for to
# compute the CLI's session-file location inside the sandbox.
SANDBOX_PROJECT_CWD = "/workspace"
SANDBOX_HOME = Path("/home/agent")


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
    #
    # api_key mode: ``messages`` carries the full conversation history
    #   ([{role, content}, ...]) accumulated by SdkAgentExecutor.
    # claude_max (oauth) mode: ``session_id`` carries the CLI session UUID;
    #   ``session_blob_path`` is the in-sandbox path to the session JSONL
    #   file (for serialization through the runner's stdout final_result),
    #   and ``session_blob`` carries the actual bytes after the orchestrator
    #   has done a ``docker cp`` to pull them out of the sandbox before it
    #   is destroyed. ``session_blob`` is what gets persisted to the
    #   ``agent_sessions`` table; ``session_blob_path`` is an in-sandbox
    #   pointer that is meaningless once the container is gone.
    messages: list[dict[str, Any]] | None = None
    session_id: str | None = None
    session_blob_path: str | None = None
    session_blob: bytes | None = None


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
                "embry0.sandbox.runner",
                "--config",
                config_json,
                *extra_args,
            ]
            proc = await self._docker.stream_exec(
                container=container,
                command=command,
                workdir="/workspace",
                # EMB-37: per-tool-call caps. The Claude CLI child inherits
                # the exec env and imposes these itself — one hung MCP call
                # (observed: 15+ min on a dead host) or runaway Bash command
                # can no longer eat the whole run budget; it errors back to
                # the agent, which can react.
                env={
                    "MCP_TOOL_TIMEOUT": "120000",
                    "BASH_DEFAULT_TIMEOUT_MS": "120000",
                    "BASH_MAX_TIMEOUT_MS": "240000",
                },
            )

            timeout_seconds: float = config.get("timeout_seconds", 300)
            # EMB-37: no-progress backstop. Hung tool calls don't advance
            # turns, so max_turns can't fire and the wall-clock kill (up to
            # 2h) was the only way out. Every stdout line from the runner is
            # a liveness heartbeat; grace must exceed the longest legal
            # silent gap (BASH_MAX 240s + model thinking), hence 360s.
            idle_grace: float = config.get("idle_grace_seconds", 360)
            idle_fired: list[bool] = []

            async def _on_idle() -> None:
                idle_fired.append(True)
                logger.error(
                    "agent_runner_idle_watchdog_fired",
                    agent_type=config.get("agent_type"),
                    container=container,
                    idle_grace_seconds=idle_grace,
                )
                try:
                    proc.kill()
                except Exception:
                    pass
                # proc.kill() only kills the host-side docker client — the
                # in-container runner (and its CLI/browser children) keeps
                # running until the sandbox is destroyed. Best-effort pkill
                # so a stuck agent stops burning tokens immediately.
                try:
                    await self._docker.run_cmd(
                        self._docker.build_exec_cmd(container, ["pkill", "-f", "embry0.sandbox.runner"]),
                        timeout=10,
                    )
                except Exception:
                    logger.warning("agent_runner_idle_pkill_failed", container=container)

            from embry0.workflows.qa.watchdogs import IdleWatchdog  # noqa: PLC0415 — leaf module, no cycle

            watchdog = IdleWatchdog(grace_seconds=idle_grace, on_timeout=_on_idle)

            async def _read_and_wait() -> AgentOutput:
                final_result: dict[str, Any] | None = None
                event_callback = on_event or (lambda _: None)
                line_count = 0
                stderr_chunks: list[bytes] = []

                async def _drain_stderr() -> None:
                    if proc.stderr is None:
                        return
                    while True:
                        chunk = await proc.stderr.read(4096)
                        if not chunk:
                            return
                        stderr_chunks.append(chunk)

                stderr_task = asyncio.create_task(_drain_stderr())

                if proc.stdout is not None:
                    async for raw_line in proc.stdout:
                        # Every raw line is liveness — heartbeat before any
                        # parsing so non-event output still counts.
                        watchdog.heartbeat()
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
                await stderr_task

                stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")

                if idle_fired:
                    return AgentOutput(
                        agent_type=config.get("agent_type", "unknown"),
                        is_error=True,
                        error_message=(
                            f"agent produced no events for {idle_grace:.0f}s (idle watchdog); run killed"
                        ),
                    )

                logger.info(
                    "agent_stdout_captured",
                    agent_type=config.get("agent_type"),
                    line_count=line_count,
                    exit_code=proc.returncode,
                )

                if proc.returncode and proc.returncode != 0:
                    logger.error(
                        "agent_runner_subprocess_failed",
                        agent_type=config.get("agent_type"),
                        exit_code=proc.returncode,
                        stderr=stderr_text[-4000:],
                    )

                if final_result:
                    output = AgentOutput(
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
                    # Plan C closeout: claude_max mode persists the CLI's
                    # JSONL session file. The file lives inside the sandbox
                    # at ``session_blob_path``; we must ``docker cp`` the
                    # bytes out BEFORE the caller destroys the sandbox.
                    # Best-effort — a missing file (race / wrong path) must
                    # not fail the agent run; the persist call upstream
                    # tolerates ``session_blob is None`` and just won't
                    # restore CLI state on the next turn.
                    if output.session_id and output.session_blob_path and not output.is_error:
                        try:
                            output.session_blob = await self._docker.copy_bytes_from(
                                container, output.session_blob_path
                            )
                        except Exception:
                            logger.warning(
                                "agent_runner_session_blob_copy_failed",
                                container=container,
                                session_id=output.session_id,
                                session_blob_path=output.session_blob_path,
                                exc_info=True,
                            )
                    return output

                trailing = stderr_text.strip()[-1500:]
                msg = "No final result received from sandbox"
                if trailing:
                    msg = f"{msg}; runner stderr: {trailing}"
                return AgentOutput(
                    agent_type=config.get("agent_type", "unknown"),
                    is_error=True,
                    error_message=msg,
                )

            watchdog.start()
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
            finally:
                watchdog.stop()

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
            fd, tmp_path = tempfile.mkstemp(prefix="embry0-resume-", suffix=".json")
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
            sandbox_target = canonical_session_path_for(
                home_dir=SANDBOX_HOME,
                session_id=resume_session.session_id,
                project_cwd=SANDBOX_PROJECT_CWD,
            )
            # docker cp does not auto-mkdir parent — ensure it exists first.
            mkdir_cmd = self._docker.build_exec_cmd(container, ["mkdir", "-p", str(sandbox_target.parent)])
            await self._docker.run_cmd(mkdir_cmd, timeout=10)
            await self._docker.copy_bytes_into(container, resume_session.session_blob, str(sandbox_target))
            return ["--session-id", resume_session.session_id]

        # Defensive: future modes should be wired explicitly.
        logger.warning("agent_runner_unknown_session_mode", mode=resume_session.mode)
        return []
