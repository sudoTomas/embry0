"""AgentExecutor — the protocol both SDK and CLI executors implement."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from embry0.agents.claude_cli_session import find_session_file
from embry0.agents.config_builder import build_sdk_options
from embry0.agents.invocation import AgentInvocation
from embry0.agents.session import render_transcript_block
from embry0.execution.agent_runner import AgentOutput
from embry0.safety.policy import SafetyPolicy, evaluate_policy, render_settings_json

if TYPE_CHECKING:
    from langchain_core.runnables.config import RunnableConfig

logger = structlog.get_logger(__name__)


class AgentExecutor(Protocol):
    async def run(
        self,
        invocation: AgentInvocation,
        config: RunnableConfig,
        *,
        resume_session_id: str | None = None,
        resume_messages: list[dict[str, Any]] | None = None,
    ) -> AgentOutput: ...


_ASK_USER_MARKER = '{"type": "agent_ask_user"'
_MAX_EMBEDDED_ASK_EVENTS = 5


def _extract_embedded_ask_user_events(text: str) -> list[dict[str, Any]]:
    """Recover agent_ask_user events embedded in a tool result (EMB-44).

    The in-sandbox ask_user helper prints one compact-JSON event per call to
    the Bash tool's stdout; by the time it reaches us it is a substring of
    the tool-result content (possibly inside a repr'd block list), so decode
    from each marker with a raw JSON decoder rather than splitting lines.
    Capped defensively — one tool call asking dozens of questions is noise,
    and the ask-user round cap governs the real limit downstream.
    """
    events: list[dict[str, Any]] = []
    idx = 0
    decoder = json.JSONDecoder()
    while len(events) < _MAX_EMBEDDED_ASK_EVENTS:
        pos = text.find(_ASK_USER_MARKER, idx)
        if pos == -1:
            break
        try:
            obj, consumed = decoder.raw_decode(text[pos:])
        except ValueError:
            idx = pos + 1
            continue
        if isinstance(obj, dict) and obj.get("question"):
            events.append(obj)
        idx = pos + consumed
    return events


def _workspace_root() -> Path:
    """Return the workspace root. Respects EMBRY0_WORKSPACE_ROOT for tests."""
    return Path(os.environ.get("EMBRY0_WORKSPACE_ROOT", "/workspace"))


def _resolve_writer(config: dict[str, Any] | None) -> Callable[[dict[str, Any]], None]:
    """Extract the stream writer from a RunnableConfig.

    Prefers langgraph.config.get_stream_writer() when present; falls back to
    a _test_writer key for unit tests; falls back to a no-op otherwise.
    """
    if config and "_test_writer" in config:
        writer_fn: Callable[[dict[str, Any]], None] = config["_test_writer"]
        return writer_fn
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
        return writer
    except Exception:
        return lambda _e: None


def _summarize_tool_input(tool_name: str, tool_input: dict[str, Any]) -> str:
    if not isinstance(tool_input, dict):
        return str(tool_input)[:200]
    if tool_name in ("Read", "Glob", "Grep"):
        result = tool_input.get("file_path", "") or tool_input.get("path", "") or tool_input.get("pattern", "")
        return str(result)
    if tool_name in ("Write", "Edit"):
        return str(tool_input.get("file_path", ""))
    if tool_name == "Bash":
        return str(tool_input.get("command", ""))[:200]
    return str(tool_input)[:200]


def _extract_hook_call(hook_input: Any) -> tuple[str, dict[str, Any]]:
    """Pull (tool_name, tool_input) out of a PreToolUse hook payload.

    The SDK's PreToolUseHookInput is a TypedDict — a plain dict at runtime —
    so the previous getattr()-based extraction ALWAYS returned ''/{}: the
    tool-scoped content rules never matched, and once EMB-37's name check
    landed, every call was denied with "tool '' is not in this agent's
    allowlist" (job-616393868ba6). Handle dict first; keep the attribute
    path for any object-shaped input a future SDK might pass. Malformed
    input degrades to ''/{} — with a non-empty allowlist that fails CLOSED
    (the name check denies ''), which is the correct failure direction for
    a safety hook.
    """
    if isinstance(hook_input, dict):
        raw_name = hook_input.get("tool_name")
        raw_input = hook_input.get("tool_input")
    else:
        raw_name = getattr(hook_input, "tool_name", None)
        raw_input = getattr(hook_input, "tool_input", None)
    name = raw_name if isinstance(raw_name, str) else ""
    tool_input = raw_input if isinstance(raw_input, dict) else {}
    return name, tool_input


async def _evaluate_hook(
    policy: SafetyPolicy,
    tool_name: str,
    tool_input: dict[str, Any],
    tools_called: dict[str, int],  # noqa: ARG001 — reserved for future per-tool counters
    writer: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Ring-3 PreToolUse hook — delegates to evaluate_policy, fail-closed.

    Returns the SDK's PreToolUseHookSpecificOutput shape:
    ``{"hookSpecificOutput": {"hookEventName": "PreToolUse",
    "permissionDecision": "allow"|"deny", "permissionDecisionReason": "..."}}``.

    The legacy top-level ``{"decision": "allow"|"deny"}`` shape that earlier
    code returned is REJECTED by the Claude CLI's stdin Zod validator
    (the schema only accepts ``decision: "block"`` at top level — observed
    on CLI 2.1.92 with claude-agent-sdk 0.1.5x). Using the newer
    ``hookSpecificOutput.permissionDecision`` form is forward-compatible.

    Exposed at module level so unit tests can exercise the deny path directly
    (a fake query() mock doesn't invoke SDK hooks, so the only way to test this
    path is to call this function directly).
    """
    if not isinstance(tool_input, dict):
        tool_input = {}
    verdict = evaluate_policy(policy, tool_name, tool_input)
    if not verdict.allowed:
        writer({"type": "error", "error": verdict.reason, "tool_name": tool_name})
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": verdict.reason,
            }
        }
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }


class SdkAgentExecutor:
    """SDK-based agent executor.

    Responsibilities:
    - Render invocation → ClaudeAgentOptions via config_builder.
    - Write Ring-2 settings.json into <workspace>/.claude/settings.json.
    - Register Ring-3 PreToolUse hook that delegates to evaluate_policy.
    - Stream SDK messages through the writer as embry0 events.
    - Aggregate into AgentOutput.

    Does NOT manage the sandbox container — that is the caller's concern.
    """

    async def run(
        self,
        invocation: AgentInvocation,
        config: RunnableConfig | dict[str, Any] | None = None,
        *,
        resume_session_id: str | None = None,
        resume_messages: list[dict[str, Any]] | None = None,
    ) -> AgentOutput:
        from claude_agent_sdk import (  # local import; SDK is optional in some contexts
            AssistantMessage,
            HookContext,
            HookMatcher,
            ResultMessage,
            TextBlock,
            ToolUseBlock,
            query,
        )
        from claude_agent_sdk.types import HookInput, HookJSONOutput

        try:
            from claude_agent_sdk import ThinkingBlock
        except ImportError:
            ThinkingBlock = None  # type: ignore[assignment,misc]
        try:
            from claude_agent_sdk import ToolResultBlock
        except ImportError:
            ToolResultBlock = None  # type: ignore[assignment,misc]

        # Duck-type message/block detection so tests can swap in fakes with
        # equivalent structure. Real SDK classes satisfy the same shape.
        def _is_assistant(msg: Any) -> bool:
            if isinstance(msg, AssistantMessage):
                return True
            return hasattr(msg, "content") and hasattr(msg, "model") and not hasattr(msg, "total_cost_usd")

        def _is_result(msg: Any) -> bool:
            if isinstance(msg, ResultMessage):
                return True
            return hasattr(msg, "total_cost_usd") or (hasattr(msg, "result") and hasattr(msg, "num_turns"))

        def _is_text_block(blk: Any) -> bool:
            if isinstance(blk, TextBlock):
                return True
            return hasattr(blk, "text") and not hasattr(blk, "name") and not hasattr(blk, "input")

        def _is_tool_use_block(blk: Any) -> bool:
            if isinstance(blk, ToolUseBlock):
                return True
            # Duck-type: has name, id, and input — but not the text attr of a TextBlock.
            return hasattr(blk, "name") and hasattr(blk, "id") and hasattr(blk, "input") and not hasattr(blk, "text")

        writer = _resolve_writer(config)  # type: ignore[arg-type]
        now = time.time()
        tools_called: dict[str, int] = {}
        output_text = ""
        cost_usd = 0.0
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
        cache_creation_tokens = 0
        is_error = False
        error_message = ""

        # --- Plan C closeout: per-mode conversation-state capture.
        #
        # api_key mode: build a [{role, content}] messages list as we go —
        #   the SDK's `query()` API does not expose its internal messages
        #   buffer publicly, so we accumulate from the prompt + each
        #   AssistantMessage's text blocks. This is sufficient for the
        #   anthropic_api / Messages-API resume path: AgentSession.messages
        #   feeds straight back as the next prompt's prior context.
        #
        # claude_max (oauth) mode: snapshot the SDK-emitted session_id from
        #   any message that carries one (AssistantMessage.session_id or
        #   ResultMessage.session_id) and emit the in-sandbox path discovered
        #   by claude_cli_session.find_session_file (typically
        #   ~/.claude/projects/<sanitized-cwd>/<id>.jsonl) as ``session_blob_path``.
        #   The orchestrator-side AgentRunner ``docker cp``s the bytes out
        #   before the sandbox is destroyed; see embry0/execution/agent_runner.py.
        captured_messages: list[dict[str, Any]] = []
        captured_session_id: str | None = None

        # --- Ring 2: write settings.json
        ws = _workspace_root()
        claude_dir = ws / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "settings.json").write_text(json.dumps(render_settings_json(invocation.safety_policy), indent=2))

        # --- Build SDK options
        options = build_sdk_options(invocation)

        # --- EMB-36: non-Anthropic provider overlay. The CLI honors
        # ANTHROPIC_BASE_URL/ANTHROPIC_API_KEY, so a compat backend (xAI
        # grok) is per-agent env configuration on the CLI subprocess. The
        # provider key rides the sandbox container env (never argv); a
        # missing key fails closed before any tokens are spent.
        if invocation.provider:
            from embry0.agents.providers import PROVIDERS

            prov = next((pr for pr in PROVIDERS if pr.name == invocation.provider), None)
            if prov is None:
                return AgentOutput(
                    agent_type=invocation.agent_type,
                    is_error=True,
                    error_message=f"unknown model provider: {invocation.provider!r}",
                )
            provider_key = os.environ.get(prov.api_key_env, "")
            if not provider_key:
                return AgentOutput(
                    agent_type=invocation.agent_type,
                    is_error=True,
                    error_message=(
                        f"model {invocation.model!r} requires {prov.api_key_env} in the "
                        "orchestrator environment (injected into the sandbox at create)"
                    ),
                )
            options.env = {
                "ANTHROPIC_BASE_URL": prov.base_url,
                "ANTHROPIC_API_KEY": provider_key,
                "ANTHROPIC_AUTH_TOKEN": "",
                "CLAUDE_CODE_OAUTH_TOKEN": "",
            }

        # --- EMB-35 session resume, in precedence order:
        # 1. ``resume_session_id`` → the CLI's own file-based resume. The
        #    session JSONL was staged to the canonical path by AgentRunner
        #    before this process started; the CLI reloads the full prior
        #    conversation (tool_use/thinking included) and its internal
        #    prompt caching applies. Works in BOTH auth modes — the session
        #    file is a CLI artifact, independent of how auth is provided.
        # 2. ``resume_messages`` → text-transcript replay fallback for
        #    sessions that only have a messages list (legacy rows, oversized
        #    or missing blob). The bounded transcript is prepended to the
        #    prompt; lossier and re-billed as input, hence fallback-only.
        if resume_session_id:
            options.resume = resume_session_id

        # --- Ring 3: PreToolUse hook as Python callable
        async def pre_tool_use_hook(  # noqa: ARG001 — tool_use_id/context unused by design
            hook_input: HookInput,
            tool_use_id: str | None,  # noqa: ARG001
            context: HookContext,  # noqa: ARG001
        ) -> HookJSONOutput:
            tool_name, hook_tool_input = _extract_hook_call(hook_input)
            # NOTE: tools_called is incremented on ToolUseBlock emission (after
            # hook allows) — not here — so denied tools don't inflate the counter.
            # Denial telemetry lives in the "error" event emitted below.
            return await _evaluate_hook(invocation.safety_policy, tool_name, hook_tool_input, tools_called, writer)  # type: ignore[return-value]

        # Attach hook to options. The SDK's support for `hooks` is asserted at
        # orchestrator startup (see _assert_sdk_supports_hooks); a per-run
        # try/except would silently fail-open and is the explicit anti-pattern
        # the 2026-04-28 review (S3) flagged. Set unconditionally.
        options.hooks = {"PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool_use_hook])]}

        # --- Prompt assembly: system_context prepended (legacy behavior).
        prompt = invocation.prompt
        if invocation.system_context:
            prompt = f"{invocation.system_context}\n\n{prompt}"
        if resume_messages and not resume_session_id:
            prompt = f"{render_transcript_block(resume_messages)}\n\n{prompt}"

        writer({"type": "agent_started", "agent": invocation.agent_type})

        # Seed the api_key-mode messages list with the user prompt as turn 0.
        # We append AssistantMessage text below; this stays empty/unused in
        # claude_max mode (the resume path uses session_id + jsonl bytes).
        captured_messages.append({"role": "user", "content": prompt})

        async def execute() -> None:
            nonlocal output_text, cost_usd, captured_session_id
            nonlocal input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens
            turn_number = 0
            async for message in query(prompt=prompt, options=options):
                # Snapshot session_id from any message that carries one.
                # Both AssistantMessage and ResultMessage expose session_id
                # in the SDK's types.py (see SystemMessage init payload).
                msg_session_id = getattr(message, "session_id", None)
                if msg_session_id and not captured_session_id:
                    captured_session_id = str(msg_session_id)
                if _is_assistant(message):
                    turn_number += 1
                    writer(
                        {
                            "type": "turn_start",
                            "turn_number": turn_number,
                            "model": getattr(message, "model", ""),
                            "node": invocation.agent_type,
                        }
                    )
                    # Accumulate this assistant turn's text into the messages
                    # buffer (api_key resume path). We collapse all text
                    # blocks in the message into a single content string;
                    # tool_use / thinking blocks are intentionally dropped
                    # because the Messages-API resume only needs the
                    # textual conversation, not the tool wire-format.
                    assistant_text = ""
                    for block in getattr(message, "content", []):
                        if _is_text_block(block):
                            block_text = str(getattr(block, "text", ""))
                            output_text += block_text
                            assistant_text += block_text
                            writer(
                                {
                                    "type": "text",
                                    "text": block_text[:2000],
                                    "node": invocation.agent_type,
                                }
                            )
                        elif _is_tool_use_block(block):
                            tn = getattr(block, "name", "") or getattr(block, "tool_name", "")
                            if tn:
                                tools_called[tn] = tools_called.get(tn, 0) + 1
                            raw_block_input = getattr(block, "input", {})
                            # Preserve full structured input on the event so
                            # downstream consumers (e.g. triage_node parsing
                            # the set_qa_decision tool call) can re-validate
                            # against a Pydantic schema. ``input`` stays as a
                            # short human-readable summary for log/UI use.
                            writer(
                                {
                                    "type": "tool_call",
                                    "tool_name": tn,
                                    "tool_id": getattr(block, "id", ""),
                                    "input": _summarize_tool_input(tn, raw_block_input),
                                    "tool_input": raw_block_input if isinstance(raw_block_input, dict) else {},
                                    "node": invocation.agent_type,
                                }
                            )
                        elif ThinkingBlock is not None and isinstance(block, ThinkingBlock):
                            writer(
                                {
                                    "type": "thinking",
                                    "text": getattr(block, "thinking", "")[:3000],
                                    "node": invocation.agent_type,
                                }
                            )
                        elif ToolResultBlock is not None and isinstance(block, ToolResultBlock):
                            result_text = str(getattr(block, "content", ""))
                            # EMB-44: the embry0.sandbox.ask_user helper emits
                            # its agent_ask_user JSON to the BASH TOOL's
                            # stdout, which the CLI captures as this tool
                            # result — it never reaches the runner's stdout
                            # event stream on its own. Re-emit any embedded
                            # ask_user events so _extract_ask_user_events can
                            # pause the pipeline for developer/review asks the
                            # same way it does for triage.
                            for embedded in _extract_embedded_ask_user_events(result_text):
                                embedded["node"] = invocation.agent_type
                                writer(embedded)
                            writer(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": getattr(block, "tool_use_id", ""),
                                    "content": result_text[:1000],
                                    "is_error": getattr(block, "is_error", False),
                                    "node": invocation.agent_type,
                                }
                            )
                    # Record this assistant turn in the messages buffer.
                    # Skip empty turns (assistant emitted only tool_use /
                    # thinking — no textual reply); they're meaningless to
                    # the Messages-API resume since it only replays text.
                    if assistant_text:
                        captured_messages.append({"role": "assistant", "content": assistant_text})
                elif _is_result(message):
                    if getattr(message, "result", None) and not output_text:
                        output_text = str(getattr(message, "result", ""))
                    cost_usd = getattr(message, "total_cost_usd", 0.0) or 0.0
                    usage = getattr(message, "usage", {}) or {}
                    input_tokens = int(usage.get("input_tokens", 0) or 0)
                    output_tokens = int(usage.get("output_tokens", 0) or 0)
                    cache_read_tokens = int(usage.get("cache_read_input_tokens", 0) or 0)
                    cache_creation_tokens = int(usage.get("cache_creation_input_tokens", 0) or 0)
                    writer(
                        {
                            "type": "cost_update",
                            "cost_usd": cost_usd,
                            "duration_ms": getattr(message, "duration_ms", 0),
                            "num_turns": getattr(message, "num_turns", 0),
                            "tokens_in": input_tokens,
                            "tokens_out": output_tokens,
                            "cache_read_tokens": cache_read_tokens,
                            "cache_creation_tokens": cache_creation_tokens,
                            "node": invocation.agent_type,
                        }
                    )

        try:
            await asyncio.wait_for(execute(), timeout=max(invocation.timeout_seconds, 0.001))
        except TimeoutError:
            is_error = True
            error_message = f"Agent timed out after {invocation.timeout_seconds}s"
            writer({"type": "error", "error": error_message})
        except Exception as exc:
            is_error = True
            error_message = str(exc)
            writer({"type": "error", "error": error_message})

        elapsed_ms = int((time.time() - now) * 1000)
        output = output_text[-10000:] if len(output_text) > 10000 else output_text

        # --- Session-state population (EMB-35: mode-agnostic).
        # The CLI writes a session JSONL regardless of auth mode, so capture
        # session_id + the canonical in-sandbox path for BOTH modes —
        # AgentRunner.copy_bytes_from() pulls the bytes out before the
        # sandbox is destroyed, and the next run resumes via ``--session-id``.
        # The captured messages list rides along as the replay-fallback data
        # source (used when the blob is missing or oversized).
        # On error we deliberately omit both: there's no useful state to
        # resume from a half-failed turn.
        out_messages: list[dict[str, Any]] | None = None
        out_session_id: str | None = None
        out_session_blob_path: str | None = None
        if not is_error:
            out_messages = list(captured_messages)
            if captured_session_id:
                out_session_id = captured_session_id
                # Use claude_cli_session as single source of truth for the
                # CLI's on-disk session file location. Returns None if the
                # CLI didn't write a session file at any known location —
                # in that case AgentRunner skips the docker-cp extract.
                home_dir = Path(os.path.expanduser("~"))
                try:
                    project_cwd: str | None = os.getcwd()
                except (FileNotFoundError, OSError):
                    project_cwd = None
                discovered = find_session_file(
                    home_dir=home_dir,
                    session_id=captured_session_id,
                    project_cwd=project_cwd,
                )
                out_session_blob_path = str(discovered) if discovered else None

        result = AgentOutput(
            agent_type=invocation.agent_type,
            is_error=is_error,
            error_message=error_message,
            output=output,
            cost_usd=cost_usd,
            duration_ms=elapsed_ms,
            tools_called=tools_called,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
            messages=out_messages,
            session_id=out_session_id,
            session_blob_path=out_session_blob_path,
        )
        writer(
            {
                "type": "agent_completed",
                "result": {
                    "agent_type": invocation.agent_type,
                    "is_error": is_error,
                    "error_message": error_message,
                    "output": output,
                    "cost_usd": cost_usd,
                    "duration_ms": elapsed_ms,
                    "tools_called": tools_called,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_tokens": cache_read_tokens,
                    "cache_creation_tokens": cache_creation_tokens,
                },
                "cost_usd": cost_usd,
                "duration_ms": elapsed_ms,
                "tools_called": tools_called,
            }
        )
        return result


def _assert_sdk_supports_hooks() -> None:
    """Verify claude_agent_sdk exposes a writable `hooks` attribute.

    Called from orchestrator startup. Refuses to return on failure so the
    orchestrator dies at boot rather than fail-opening on the first agent run.
    """
    try:
        from claude_agent_sdk import ClaudeAgentOptions
    except ImportError as exc:
        raise RuntimeError(
            "claude_agent_sdk is not importable — refusing to start. "
            "Install/pin the SDK before launching the orchestrator."
        ) from exc

    sentinel = {"_embry0_hook_check": True}
    try:
        opts = ClaudeAgentOptions()
        opts.hooks = sentinel  # type: ignore[assignment]
        if getattr(opts, "hooks", None) is not sentinel:
            raise RuntimeError("read-back mismatch")
    except Exception as exc:
        raise RuntimeError(
            "Installed claude_agent_sdk does not expose a writable `hooks` "
            "attribute. Ring-3 PreToolUse cannot be enforced. Refusing to "
            f"start. (exception: {exc!r})"
        ) from exc

    logger.info("sdk_hooks_supported")
