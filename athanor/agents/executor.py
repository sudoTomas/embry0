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

from athanor.agents.config_builder import build_sdk_options
from athanor.agents.invocation import AgentInvocation
from athanor.execution.agent_runner import AgentOutput
from athanor.safety.policy import SafetyPolicy, evaluate_policy, render_settings_json

if TYPE_CHECKING:
    from langchain_core.runnables.config import RunnableConfig

logger = structlog.get_logger(__name__)


class AgentExecutor(Protocol):
    async def run(
        self,
        invocation: AgentInvocation,
        config: RunnableConfig,
    ) -> AgentOutput: ...


def _workspace_root() -> Path:
    """Return the workspace root. Respects ATHANOR_WORKSPACE_ROOT for tests."""
    return Path(os.environ.get("ATHANOR_WORKSPACE_ROOT", "/workspace"))


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


async def _evaluate_hook(
    policy: SafetyPolicy,
    tool_name: str,
    tool_input: dict[str, Any],
    tools_called: dict[str, int],  # noqa: ARG001 — reserved for future per-tool counters
    writer: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Ring-3 PreToolUse hook — delegates to evaluate_policy, fail-closed.

    Exposed at module level so unit tests can exercise the deny path directly
    (a fake query() mock doesn't invoke SDK hooks, so the only way to test this
    path is to call this function directly).
    """
    if not isinstance(tool_input, dict):
        tool_input = {}
    verdict = evaluate_policy(policy, tool_name, tool_input)
    if not verdict.allowed:
        writer({"type": "error", "error": verdict.reason, "tool_name": tool_name})
        return {"decision": "deny", "reason": verdict.reason}
    return {"decision": "allow"}


class SdkAgentExecutor:
    """SDK-based agent executor.

    Responsibilities:
    - Render invocation → ClaudeAgentOptions via config_builder.
    - Write Ring-2 settings.json into <workspace>/.claude/settings.json.
    - Register Ring-3 PreToolUse hook that delegates to evaluate_policy.
    - Stream SDK messages through the writer as Athanor events.
    - Aggregate into AgentOutput.

    Does NOT manage the sandbox container — that is the caller's concern.
    """

    async def run(
        self,
        invocation: AgentInvocation,
        config: RunnableConfig | dict[str, Any] | None = None,
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
        is_error = False
        error_message = ""

        # --- Ring 2: write settings.json
        ws = _workspace_root()
        claude_dir = ws / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "settings.json").write_text(json.dumps(render_settings_json(invocation.safety_policy), indent=2))

        # --- Build SDK options
        options = build_sdk_options(invocation)

        # --- Ring 3: PreToolUse hook as Python callable
        async def pre_tool_use_hook(  # noqa: ARG001 — tool_use_id/context unused by design
            hook_input: HookInput,
            tool_use_id: str | None,  # noqa: ARG001
            context: HookContext,  # noqa: ARG001
        ) -> HookJSONOutput:
            tool_name = getattr(hook_input, "tool_name", None) or ""
            raw_tool_input = getattr(hook_input, "tool_input", None)
            hook_tool_input: dict[str, Any] = raw_tool_input if isinstance(raw_tool_input, dict) else {}
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

        writer({"type": "agent_started", "agent": invocation.agent_type})

        async def execute() -> None:
            nonlocal output_text, cost_usd
            turn_number = 0
            async for message in query(prompt=prompt, options=options):
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
                    for block in getattr(message, "content", []):
                        if _is_text_block(block):
                            block_text = str(getattr(block, "text", ""))
                            output_text += block_text
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
                            writer(
                                {
                                    "type": "tool_call",
                                    "tool_name": tn,
                                    "tool_id": getattr(block, "id", ""),
                                    "input": _summarize_tool_input(tn, getattr(block, "input", {})),
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
                            writer(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": getattr(block, "tool_use_id", ""),
                                    "content": str(getattr(block, "content", ""))[:1000],
                                    "is_error": getattr(block, "is_error", False),
                                    "node": invocation.agent_type,
                                }
                            )
                elif _is_result(message):
                    if getattr(message, "result", None) and not output_text:
                        output_text = str(getattr(message, "result", ""))
                    cost_usd = getattr(message, "total_cost_usd", 0.0) or 0.0
                    usage = getattr(message, "usage", {}) or {}
                    writer(
                        {
                            "type": "cost_update",
                            "cost_usd": cost_usd,
                            "duration_ms": getattr(message, "duration_ms", 0),
                            "num_turns": getattr(message, "num_turns", 0),
                            "tokens_in": usage.get("input_tokens", 0),
                            "tokens_out": usage.get("output_tokens", 0),
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
        result = AgentOutput(
            agent_type=invocation.agent_type,
            is_error=is_error,
            error_message=error_message,
            output=output,
            cost_usd=cost_usd,
            duration_ms=elapsed_ms,
            tools_called=tools_called,
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

    sentinel = {"_athanor_hook_check": True}
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
