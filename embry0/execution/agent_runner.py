"""Orchestrator-side agent runner — bridges LangGraph and sandbox.

Handles: docker exec into sandbox, stdout stream parsing, event
forwarding to LangGraph, and AgentOutput construction.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from embry0.agents.claude_cli_session import canonical_session_path_for
from embry0.agents.session import MAX_RESUME_BLOB_BYTES
from embry0.execution.docker_client import DockerClient, ExecStdinTimeoutError
from embry0.execution.events import parse_event
from embry0.safety.error_codes import ErrorCode, classify_agent_error
from embry0.safety.redaction import redact_secrets, redacted_log_field, redacted_stderr_tail, secret_env_values

if TYPE_CHECKING:
    # SandboxManager is only used as an __init__ type annotation. Importing
    # it eagerly would pull in embry0.execution.image_registry and the
    # whole image-build chain, which is not needed inside the sandbox where
    # this module is loaded only for the AgentOutput dataclass.
    from embry0.agents.session import AgentSession
    from embry0.execution.sandbox_manager import SandboxManager

logger = structlog.get_logger(__name__)


# Sandbox-side path where the orchestrator stages the prior message list
# for the text-replay fallback. Tmpfs-backed and disposed with the sandbox.
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
    # Safety-policy denials this run, keyed by reason. Empty for runs that
    # never tripped a rule; a non-empty counter is usually the explanation
    # for a turn-cap death.
    policy_denials: dict[str, int] = field(default_factory=dict)
    # EMB-35: per-run token usage from ResultMessage.usage. 0 when the run
    # errored before a result message or on legacy wire payloads.
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
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
    # A specific ErrorCode value when the runner itself knows why the run
    # failed (#304: stale image, stalled exec). None ⇒ the workflow decides.
    error_code: str | None = None
    # #466: when the provider says the account's usage window resets, for an
    # ERR_PROVIDER_QUOTA_EXHAUSTED run only. Aware UTC. CredentialHealth
    # quarantines the account until then instead of for a fixed interval.
    provider_resets_at: datetime | None = None
    # #281: the last few KiB of the runner's stderr, REDACTED, on an errored
    # run only. It is the one place an agent CLI crash ("Command failed with
    # exit code 1 — check stderr") explains itself. Stored on the trace row
    # for the dashboard / job_get; never posted to a tracker.
    stderr_tail: str = ""


# Cap on stderr held in memory while a run is in flight; only the tail is
# ever used, and a chatty MCP server can write for hours.
_STDERR_BUFFER_MAX_BYTES = 256 * 1024


def aware_datetime(value: Any) -> datetime | None:
    """An ISO-8601 timestamp with an offset as a datetime, else None.

    Reads ``provider_resets_at`` off the runner's final_result (and off state,
    where it rides as a string). A runner image older than #466 sends no such
    key; a naive or malformed value is dropped rather than guessed at.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.utcoffset() is not None else None


def _diagnose(output: AgentOutput, stderr_text: str) -> AgentOutput:
    """Give an errored run its code and its stderr tail. One exit point for
    every way a run ends, so no failure path can forget either."""
    if not output.is_error:
        return output
    if not output.error_code:
        code = classify_agent_error(output.error_message)
        output.error_code = code.value if code else None
    output.stderr_tail = redacted_stderr_tail(stderr_text)
    return output


# #354: stderr that says the runner died in Python before the agent started.
# A bad interpreter invocation or a missing / inconsistent baked module (a
# partial image refresh: new agent_runner.py beside an old embry0/safety/)
# fails here, before the runner's own error handling can report anything.
_START_CRASH_MARKERS = ("Traceback (most recent call last)", "ImportError", "ModuleNotFoundError", "No module named")


def _is_start_crash(exit_code: int | None, stdout_lines: int, stderr: str) -> bool:
    """The sandbox could not start the agent: a non-zero exit before the
    runner wrote one stdout line (it emits events from its first action),
    with Python's own failure on stderr."""
    return bool(exit_code) and stdout_lines == 0 and any(marker in stderr for marker in _START_CRASH_MARKERS)


# #304 stale-image fallback. Linux caps ONE argv string at MAX_ARG_STRLEN
# (128 KiB); stay well under it so `docker exec` framing never tips it over.
_ARGV_FALLBACK_MAX_BYTES = 100 * 1024
# What argparse prints when a runner that predates --config-stdin is handed it.
_STALE_RUNNER_STDERR_MARKER = "arguments are required: --config"


@dataclass
class _RunnerExit:
    """How one runner exec ended. Defaults mean "did not exit on its own"
    (wall-clock kill, stalled stdin), which never qualifies for a retry."""

    exit_code: int | None = None
    stdout_lines: int = 0
    stderr: str = ""

    def is_stale_runner_usage_error(self) -> bool:
        """True only for argparse's usage exit BEFORE any agent work: exit
        code 2, not one stdout line (the runner emits events on stdout from
        its first action), and the missing---config usage text on stderr."""
        return self.exit_code == 2 and self.stdout_lines == 0 and _STALE_RUNNER_STDERR_MARKER in self.stderr


class AgentRunner:
    """Runs agents inside sandbox containers via docker exec."""

    def __init__(
        self,
        sandbox_manager: SandboxManager,
        docker: DockerClient,
        *,
        on_stale_image: Callable[[str], None] | None = None,
    ) -> None:
        self._sandbox = sandbox_manager
        self._docker = docker
        # #285: called with "argv_fallback" / "config_too_large" when a stale
        # runner image is met. A plain callable, not the metrics facade: this
        # module is baked into the sandbox image, which ships neither
        # embry0.metrics nor prometheus_client.
        self._on_stale_image = on_stale_image or (lambda _outcome: None)

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
            # #304: the config rides stdin, never argv. One argv string is
            # capped at 128 KiB (MAX_ARG_STRLEN); a review prompt carrying a
            # PR diff blew past it and the exec died with "[Errno 7] Argument
            # list too long" after the developer had already pushed. It also
            # keeps prompts out of the host's process table.
            runner = ["python", "-m", "embry0.sandbox.runner"]
            output, exit_facts = await self._exec_runner(
                container, [*runner, "--config-stdin", *extra_args], config_json.encode(), config, on_event
            )
            if not exit_facts.is_stale_runner_usage_error():
                return output

            # The image's runner predates --config-stdin (prebaked per-repo QA
            # images are `docker commit`s of an old sandbox; profile images
            # are built FROM it — neither is rebuilt by the deploy recipe).
            # argparse rejected the flag before the runner imported anything,
            # so no agent work has happened and one argv retry cannot
            # double-execute.
            image = await self._image_ref(container)
            if len(config_json.encode()) > _ARGV_FALLBACK_MAX_BYTES:
                logger.error("sandbox_runner_stale_image_config_too_large", image=image, container=container)
                self._on_stale_image("config_too_large")
                return AgentOutput(
                    agent_type=config.get("agent_type", "unknown"),
                    is_error=True,
                    error_message=(
                        f"sandbox image {image!r} has a runner that predates --config-stdin and this agent "
                        f"config ({len(config_json.encode())} bytes) is too large for argv — rebuild the image"
                    ),
                    error_code=ErrorCode.SANDBOX_IMAGE_STALE.value,
                )
            logger.warning(
                "sandbox_runner_stale_image",
                image=image,
                container=container,
                agent_type=config.get("agent_type"),
                hint="runner predates --config-stdin; config sent on argv — rebuild this image",
            )
            self._on_stale_image("argv_fallback")
            output, _ = await self._exec_runner(
                container, [*runner, "--config", config_json, *extra_args], None, config, on_event
            )
            return output

        finally:
            if network:
                try:
                    await self._sandbox.disconnect_network(container, network)
                except RuntimeError:
                    logger.warning("network_disconnect_failed", container=container, network=network)

    async def _exec_runner(
        self,
        container: str,
        command: list[str],
        stdin_data: bytes | None,
        config: dict[str, Any],
        on_event: Callable[[dict[str, Any]], None] | None,
    ) -> tuple[AgentOutput, _RunnerExit]:
        """One `docker exec` of the sandbox runner: stream events, return the
        result plus how the process ended (``_RunnerExit`` stays at its
        defaults when the run was killed by the wall-clock timeout)."""
        exit_facts = _RunnerExit()
        try:
            proc = await self._docker.stream_exec(
                container=container,
                command=command,
                workdir="/workspace",
                stdin_data=stdin_data,
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
        except ExecStdinTimeoutError as exc:
            return (
                AgentOutput(
                    agent_type=config.get("agent_type", "unknown"),
                    is_error=True,
                    error_message=f"sandbox exec stalled before the agent started: {exc}",
                    error_code=ErrorCode.SANDBOX_EXEC_STALLED.value,
                ),
                exit_facts,
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
            if not await self._stop_runner_in_sandbox(container):
                logger.warning("agent_runner_idle_pkill_failed", container=container)

        from embry0.workflows.qa.watchdogs import IdleWatchdog  # noqa: PLC0415 — leaf module, no cycle

        watchdog = IdleWatchdog(grace_seconds=idle_grace, on_timeout=_on_idle)

        # Shared with the timeout path below, which returns without
        # _read_and_wait ever finishing.
        stderr_chunks: deque[bytes] = deque()

        def _stderr_text() -> str:
            return b"".join(stderr_chunks).decode("utf-8", errors="replace")

        async def _read_and_wait() -> AgentOutput:
            final_result: dict[str, Any] | None = None
            event_callback = on_event or (lambda _: None)
            line_count = 0
            buffered = 0

            async def _drain_stderr() -> None:
                nonlocal buffered
                if proc.stderr is None:
                    return
                while True:
                    chunk = await proc.stderr.read(4096)
                    if not chunk:
                        return
                    stderr_chunks.append(chunk)
                    buffered += len(chunk)
                    while buffered > _STDERR_BUFFER_MAX_BYTES and len(stderr_chunks) > 1:
                        buffered -= len(stderr_chunks.popleft())

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

            stderr_text = _stderr_text()
            exit_facts.exit_code = proc.returncode
            exit_facts.stdout_lines = line_count
            exit_facts.stderr = stderr_text

            if idle_fired:
                return AgentOutput(
                    agent_type=config.get("agent_type", "unknown"),
                    is_error=True,
                    error_message=(f"agent produced no events for {idle_grace:.0f}s (idle watchdog); run killed"),
                )

            logger.info(
                "agent_stdout_captured",
                agent_type=config.get("agent_type"),
                line_count=line_count,
                exit_code=proc.returncode,
            )

            if proc.returncode and proc.returncode != 0:
                # #281: this line ships to Loki — same scrubber as the stored tail.
                # error_message/error_code/result are on the final_result already
                # (stdout), but this event used to log only stderr. The runner
                # exits 1 with an empty stderr for every is_error result, so the
                # next tick had nothing but the exit code.
                secrets = secret_env_values()
                payload = final_result or {}
                logger.error(
                    "agent_runner_subprocess_failed",
                    agent_type=config.get("agent_type"),
                    exit_code=proc.returncode,
                    stderr=redact_secrets(stderr_text[-4000:], secrets),
                    error_message=redacted_log_field(str(payload.get("error_message") or ""), secrets),
                    error_code=payload.get("error_code") or None,
                    result=redacted_log_field(str(payload.get("output") or ""), secrets),
                    diagnostic=redacted_log_field(str(payload.get("diagnostic") or ""), secrets),
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
                    policy_denials=final_result.get("policy_denials", {}) or {},
                    input_tokens=int(final_result.get("input_tokens", 0) or 0),
                    output_tokens=int(final_result.get("output_tokens", 0) or 0),
                    cache_read_tokens=int(final_result.get("cache_read_tokens", 0) or 0),
                    cache_creation_tokens=int(final_result.get("cache_creation_tokens", 0) or 0),
                    error_code=final_result.get("error_code") or None,
                    provider_resets_at=aware_datetime(final_result.get("provider_resets_at")),
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
                # No is_error gate here (RAV-1260): the executor is the
                # single authority on whether an errored run's session is
                # worth keeping — it only sets session_blob_path when it
                # is (turn-cap deaths, for the decide-now recovery).
                if output.session_id and output.session_blob_path:
                    try:
                        output.session_blob = await self._docker.copy_bytes_from(container, output.session_blob_path)
                    except Exception:
                        logger.warning(
                            "agent_runner_session_blob_copy_failed",
                            container=container,
                            session_id=output.session_id,
                            session_blob_path=output.session_blob_path,
                            exc_info=True,
                        )
                return output

            trailing = redact_secrets(stderr_text.strip()[-1500:], secret_env_values())
            if _is_start_crash(proc.returncode, line_count, stderr_text):
                # #354: not "the agent returned nothing" — it never started.
                return AgentOutput(
                    agent_type=config.get("agent_type", "unknown"),
                    is_error=True,
                    error_message=(
                        f"The sandbox could not start the agent: the runner exited {proc.returncode} "
                        f"before its first event; runner stderr: {trailing}"
                    ),
                    error_code=ErrorCode.SANDBOX_AGENT_START_FAILED.value,
                )
            msg = "No final result received from sandbox"
            if trailing:
                msg = f"{msg}; runner stderr: {trailing}"
            return AgentOutput(
                agent_type=config.get("agent_type", "unknown"),
                is_error=True,
                error_message=msg,
                # The diagnosis is already certain here, and it is set
                # explicitly BECAUSE this message embeds the agent's stderr:
                # left to the text classifier, a traceback that merely
                # mentions a turn cap would be read as one.
                error_code=ErrorCode.AGENT_NO_RESULT.value,
            )

        watchdog.start()
        try:
            result = await asyncio.wait_for(_read_and_wait(), timeout=timeout_seconds)
        except TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            # A timed-out job pauses and keeps its sandbox, so a runner left
            # running there would spend unseen and race a resume.
            runner_stopped = await self._stop_runner_in_sandbox(container)
            logger.error(
                "agent_runner_wall_clock_timeout",
                agent_type=config.get("agent_type"),
                container=container,
                timeout_seconds=timeout_seconds,
                runner_stopped=runner_stopped,
            )
            return (
                _diagnose(
                    AgentOutput(
                        agent_type=config.get("agent_type", "unknown"),
                        is_error=True,
                        error_message=f"Agent timed out after {timeout_seconds}s",
                    ),
                    _stderr_text(),
                ),
                exit_facts,
            )
        finally:
            watchdog.stop()

        return _diagnose(result, _stderr_text()), exit_facts

    async def _stop_runner_in_sandbox(self, container: str) -> bool:
        """Best-effort kill of the agent runner inside ``container``.

        ``proc.kill()`` only kills the host-side docker exec client: the
        in-container runner, and its CLI and browser children, keep running
        and spending until the sandbox is destroyed. True when the pkill was
        delivered; it exits 1 when nothing matched, and docker can hang, so
        any failure is False, never an exception.
        """
        try:
            await self._docker.run_cmd(
                self._docker.build_exec_cmd(container, ["pkill", "-f", "embry0.sandbox.runner"]),
                timeout=10,
            )
        except Exception:
            return False
        return True

    async def _image_ref(self, container: str) -> str:
        """The image a container was created from, for the stale-image log."""
        try:
            cmd = self._docker.build_inspect_image_cmd(container)
            return (await self._docker.run_cmd(cmd, timeout=10)).strip() or "unknown"
        except Exception:
            return "unknown"

    async def _stage_resume_session(
        self,
        container: str,
        resume_session: AgentSession | None,
    ) -> list[str]:
        """Copy the prior session into ``container`` and return runner CLI args.

        Mode-agnostic precedence (EMB-35 — the CLI's session file works under
        either auth mode):

        1. ``session_id`` + ``session_blob`` (within :data:`MAX_RESUME_BLOB_BYTES`)
           → stream the JSONL bytes to the CLI's canonical session path and
           return ``["--session-id", <id>]``; the executor sets
           ``options.resume`` and the CLI reloads the full conversation.
        2. ``messages`` → dump to a host tempfile, ``docker cp`` to
           ``RESUME_BLOB_SANDBOX_PATH``, return ``["--session-blob", <path>]``;
           the executor prepends a bounded text-transcript replay.
        3. Neither (or ``resume_session is None``) → ``[]``, fresh run.
        """
        if resume_session is None:
            return []

        if resume_session.session_blob and resume_session.session_id:
            if len(resume_session.session_blob) > MAX_RESUME_BLOB_BYTES:
                logger.warning(
                    "resume_skipped_blob_too_large",
                    session_id=resume_session.session_id,
                    agent_type=resume_session.agent_type,
                    blob_bytes=len(resume_session.session_blob),
                    max_bytes=MAX_RESUME_BLOB_BYTES,
                )
            else:
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

        if resume_session.messages:
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

        return []
