"""The agentic tool loop shared by the CLI-free executors.

One loop, any wire format. ``DirectXaiExecutor`` drives it over the Anthropic
Messages API (pointed at the xai-proxy); another API needs only a new wire.

The loop owns everything the Claude CLI gives for free, which both wires need in
the same shape:

- the builtin tools (Read/Write/Edit/Bash/Glob/Grep, :mod:`embry0.agents.xai_tools`)
  and the declared MCP stdio servers (:mod:`embry0.agents.mcp_client`);
- Ring-3 (tool name + dangerous Bash) and Ring-2 (filesystem deny globs) enforced
  per call by :func:`embry0.safety.policy.gate_tool_call` before dispatch, fail-closed;
- the stdout event wire (agent_started/turn_start/turn_usage/text/thinking/tool_call/
  tool_result/error/cost_update/agent_completed), so AgentRunner is unchanged;
- cost from token usage times the catalog price (these APIs report no run cost);
- the text-only conversation capture the resume fallback replays;
- ask_user recovery from Bash output, and the turn cap reported as ERR_AGENT_MAX_TURNS.

A wire owns only what differs between the APIs: the tool schema shape, one
request/response round trip, how tool results are sent back, and how a failed
call is classified. Each executor passes its own seams (``make_wire``,
``execute_builtin``, ``make_mcp_client``) so tests can replace them per module.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from embry0.agents._common import make_scrubber
from embry0.agents.executor import (
    _extract_embedded_ask_user_events,
    _ids,
    _resolve_writer,
    _summarize_tool_input,
    _workspace_root,
)
from embry0.agents.invocation import AgentInvocation
from embry0.agents.mcp_client import (
    MCP_TOOL_PREFIX,
    McpStdioClient,
    anthropic_tool_defs,
    call_mcp_tool,
    summarize_content,
)
from embry0.agents.output_text import FinalText
from embry0.agents.providers import price_usage
from embry0.agents.xai_tools import BUILTIN_TOOL_NAMES, tool_defs
from embry0.execution import events
from embry0.execution.agent_runner import AgentOutput
from embry0.safety.error_codes import ErrorCode
from embry0.safety.policy import gate_tool_call

if TYPE_CHECKING:
    from langchain_core.runnables.config import RunnableConfig

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT_BASE = """You are an autonomous software engineering agent operating inside \
an isolated sandbox. Your working directory is /workspace. Use the provided tools to inspect \
and modify the repository:

- Read: read a file (use before editing).
- Write: create or overwrite a file.
- Edit: replace an exact string in a file.
- Bash: run shell commands in /workspace.
- Glob: find files by pattern.
- Grep: search file contents.

Work within /workspace. Reads/writes to host-sensitive paths (/etc, /root, ~/.ssh, credentials) \
are blocked. When the task is complete, stop and summarize what you did. Do not ask for \
confirmation on reversible actions that follow from the task."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool the model asked for, in the wire-neutral shape the loop dispatches."""

    id: str
    name: str
    input: dict[str, Any]
    input_error: str = ""
    """Set when the wire could not decode the arguments: the call is answered with
    this text as an error and never executed."""


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_id: str
    content: Any
    """A string, or for an MCP tool its content block list (the Anthropic wire sends
    it as is; the chat-completions wire sends its text)."""
    is_error: bool


@dataclass(frozen=True, slots=True)
class Turn:
    """One model response."""

    parts: list[tuple[str, str]]
    """``("text" | "thinking", text)`` in the order the model produced them."""
    tool_calls: list[ToolCall]
    wants_tools: bool
    """The model stopped to have tools run (Anthropic ``stop_reason == "tool_use"``)."""
    usage: Usage | None
    assistant_message: dict[str, Any]
    """The assistant turn as the next request must carry it."""


class Wire(Protocol):
    def tool_defs(self, defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """``defs`` (Anthropic tool schemas) in the shape this API takes."""
        ...

    async def turn(self, *, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> Turn: ...

    def tool_result_messages(self, results: list[ToolResult]) -> list[dict[str, Any]]:
        """The messages that answer one turn's tool calls."""
        ...

    def failure(self, exc: BaseException) -> tuple[str | None, str]:
        """``(error code, message)`` for a call that raised."""
        ...

    async def close(self) -> None: ...


def merge_consecutive_roles(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join back-to-back text messages of one role into one, so roles alternate.

    The resume capture (``captured``) holds one assistant entry per turn, so a replayed
    transcript has assistant messages back to back. Anthropic merges consecutive turns server
    side; the compatible APIs do not document doing so. Block-content messages are left as
    they are — only plain-text entries are joined.
    """
    merged: list[dict[str, Any]] = []
    for message in messages:
        prev = merged[-1] if merged else None
        if (
            prev is not None
            and prev.get("role") == message.get("role")
            and isinstance(prev.get("content"), str)
            and isinstance(message.get("content"), str)
        ):
            merged[-1] = {**prev, "content": f"{prev['content']}\n\n{message['content']}"}
        else:
            merged.append(dict(message))
    return merged


def error_output(agent_type: str, message: str) -> AgentOutput:
    return AgentOutput(agent_type=agent_type, is_error=True, error_message=message)


async def close_mcp_clients(clients: dict[str, McpStdioClient]) -> None:
    """Best-effort teardown — a close failure must not mask the run's outcome."""
    for server_name, mcp in clients.items():
        try:
            await mcp.close()
        except Exception:
            logger.warning("mcp_client_close_failed", server=server_name, exc_info=True)


async def run_tool_loop(
    invocation: AgentInvocation,
    config: RunnableConfig | dict[str, Any] | None,
    *,
    make_wire: Callable[[], Wire],
    execute_builtin: Callable[..., tuple[str, bool]],
    make_mcp_client: Callable[[str, Any], McpStdioClient],
    compute_cost: Callable[[str, int, int, int, int], float | None],
    resume_messages: list[dict[str, Any]] | None,
    unpriced_log_event: str,
) -> AgentOutput:
    """Run ``invocation`` to completion over ``make_wire()``. Implements AgentExecutor.run's contract."""
    writer = _resolve_writer(config)  # type: ignore[arg-type]
    started = time.time()
    agent_type = invocation.agent_type
    ids = _ids(invocation)
    scrub = make_scrubber()

    allowed = [t for t in invocation.tools if t in BUILTIN_TOOL_NAMES]
    defs = tool_defs(allowed)

    # Spawn declared MCP stdio servers and expose their tools. Fail loud — an agent
    # whose config declares MCP (the QA agent) cannot do its job without it, so a
    # broken server is a job error, not a degraded run.
    mcp_clients: dict[str, McpStdioClient] = {}
    if invocation.mcp_servers:
        try:
            for server_name, server_cfg in invocation.mcp_servers.items():
                mcp = make_mcp_client(server_name, server_cfg)
                mcp_clients[server_name] = mcp
                await mcp.start()
                defs.extend(anthropic_tool_defs(server_name, await mcp.list_tools(), invocation.tools))
        except Exception as exc:
            await close_mcp_clients(mcp_clients)
            message = f"MCP server startup failed: {exc}"
            writer(events.error(message=message, code=ErrorCode.SANDBOX_INIT.value, **ids))
            return error_output(agent_type, message)

    system = SYSTEM_PROMPT_BASE
    if invocation.system_prompt:
        system = f"{system}\n\n{invocation.system_prompt}"

    prompt = invocation.prompt
    if invocation.system_context:
        prompt = f"{invocation.system_context}\n\n{prompt}"

    # Live conversation (full turns) sent back each turn.
    messages: list[dict[str, Any]] = []
    if resume_messages:
        messages.extend(resume_messages)
    messages.append({"role": "user", "content": prompt})
    messages = merge_consecutive_roles(messages)

    # Text-only capture for the resume fallback (AgentOutput.messages).
    captured: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

    cwd = str(_workspace_root())
    policy = invocation.safety_policy

    totals = {"in": 0, "out": 0, "cache_read": 0, "cache_creation": 0, "turns": 0}
    tools_called: dict[str, int] = {}
    policy_denials: dict[str, int] = {}
    final_text = FinalText()
    is_error = False
    error_message = ""
    error_code: str | None = None
    # Set when the loop runs out of turns while the model is still calling tools.
    capped = False

    wire = make_wire()
    tools = wire.tool_defs(defs)

    writer(events.agent_started(model=invocation.model, max_turns=invocation.max_turns, **ids))

    async def execute() -> None:
        nonlocal capped
        turn_number = 0
        while turn_number < invocation.max_turns:
            turn_number += 1
            totals["turns"] = turn_number
            turn = await wire.turn(system=system, tools=tools, messages=messages)
            usage = turn.usage
            if usage is not None:
                totals["in"] += usage.input_tokens
                totals["out"] += usage.output_tokens
                totals["cache_read"] += usage.cache_read_tokens
                totals["cache_creation"] += usage.cache_creation_tokens

            writer(events.turn_start(turn_number=turn_number, model=invocation.model, **ids))
            if usage is not None:
                writer(
                    events.turn_usage(
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cache_read_tokens=usage.cache_read_tokens,
                        cache_creation_tokens=usage.cache_creation_tokens,
                        cost_usd=price_usage(
                            invocation.model,
                            input_tokens=usage.input_tokens,
                            output_tokens=usage.output_tokens,
                            cache_read_tokens=usage.cache_read_tokens,
                            cache_creation_tokens=usage.cache_creation_tokens,
                        ),
                        model=invocation.model,
                        **ids,
                    )
                )

            assistant_text = ""
            for kind, text in turn.parts:
                if kind == "text":
                    assistant_text += text
                    writer(events.text(text=scrub(text), **ids))
                else:
                    writer(events.thinking(text=scrub(text), **ids))

            # Append the assistant turn so the next request has context.
            messages.append(turn.assistant_message)
            if assistant_text:
                captured.append({"role": "assistant", "content": assistant_text})
            # #329: one entry per message; joined with a blank line at the end.
            final_text.add_message(assistant_text)

            if not turn.wants_tools or not turn.tool_calls:
                return  # end of turn / output cap / no tools requested → done

            # Execute each requested tool, gating first; collect the results.
            results: list[ToolResult] = []
            for call in turn.tool_calls:
                name = call.name
                tool_id = call.id
                tool_input = call.input
                tools_called[name] = tools_called.get(name, 0) + 1
                writer(
                    events.tool_call(
                        tool_name=name,
                        tool_id=tool_id,
                        summary=_summarize_tool_input(name, tool_input),
                        tool_input=tool_input,
                        **ids,
                    )
                )

                if call.input_error:
                    results.append(ToolResult(tool_id, call.input_error, True))
                    writer(events.tool_result(tool_id=tool_id, content=scrub(call.input_error), is_error=True, **ids))
                    continue

                verdict = gate_tool_call(policy, name, tool_input, cwd=cwd)
                if not verdict.allowed:
                    # Keyed by the policy's reason, as the Claude path keys it
                    # (executor.py _evaluate_hook), so every path reports one shape.
                    denial_key = verdict.reason or name
                    policy_denials[denial_key] = policy_denials.get(denial_key, 0) + 1
                    writer(
                        events.error(
                            message=verdict.reason or "",
                            code=ErrorCode.TOOL_DENIED.value,
                            tool_name=name,
                            **ids,
                        )
                    )
                    # The result the model sees; the event goes out through events.tool_result.
                    results.append(ToolResult(tool_id, verdict.reason, True))
                    writer(events.tool_result(tool_id=tool_id, content=verdict.reason or "", is_error=True, **ids))
                    continue

                result_content: str | list[dict[str, Any]]
                if name.startswith(MCP_TOOL_PREFIX):
                    result_content, tool_err = await call_mcp_tool(mcp_clients, name, tool_input)
                    result_text = summarize_content(result_content)
                else:
                    result_text, tool_err = execute_builtin(name, tool_input, cwd=cwd, policy=policy)
                    result_content = result_text
                    # ask_user parity: the Bash ask_user helper prints its event to stdout,
                    # which lands in this tool result — recover + re-emit it (EMB-44).
                    if name == "Bash":
                        for embedded in _extract_embedded_ask_user_events(result_text):
                            writer({**embedded, **ids})
                results.append(ToolResult(tool_id, result_content, tool_err))
                writer(events.tool_result(tool_id=tool_id, content=scrub(result_text), is_error=tool_err, **ids))

            messages.extend(wire.tool_result_messages(results))
        # Loop exhausted max_turns without a natural stop.
        capped = True
        writer(
            events.error(
                message=f"reached max_turns ({invocation.max_turns})",
                code=ErrorCode.AGENT_MAX_TURNS.value,
                **ids,
            )
        )

    try:
        await asyncio.wait_for(execute(), timeout=max(invocation.timeout_seconds, 0.001))
    except TimeoutError:
        is_error = True
        error_message = f"Agent timed out after {invocation.timeout_seconds}s"
        writer(events.error(message=error_message, code=None, **ids))
    except Exception as exc:
        is_error = True
        error_code, error_message = wire.failure(exc)
        writer(events.error(message=error_message, code=error_code, **ids))
    finally:
        await wire.close()
        await close_mcp_clients(mcp_clients)

    # RAV-1260: a turn-cap death is an error, and the one error whose conversation is kept —
    # triage/review resume it once to decide now, the developer node commits the unfinished
    # work. Both key on is_error + is_max_turns_error(), as the CLI executor reports it.
    if capped and not is_error:
        is_error = True
        error_message = f"reached max_turns ({invocation.max_turns})"
        error_code = ErrorCode.AGENT_MAX_TURNS.value

    elapsed_ms = int((time.time() - started) * 1000)
    # None when the model has no price entry (#219): the price table is the only
    # accounting these APIs leave, so an unpriced model is an error to record,
    # never a silent $0.
    priced = compute_cost(
        invocation.model,
        totals["in"],
        totals["out"],
        totals["cache_read"],
        totals["cache_creation"],
    )
    cost = priced if priced is not None else 0.0
    if priced is None:
        unpriced_message = (
            f"cost unknown: model {invocation.model!r} has no price entry in "
            "embry0/agents/providers.py, so this run's "
            f"{totals['in']} input and {totals['out']} output tokens are unmetered"
        )
        logger.error(
            unpriced_log_event,
            model=invocation.model,
            tokens_in=totals["in"],
            tokens_out=totals["out"],
        )
        writer(events.error(message=unpriced_message, code=ErrorCode.INVALID_CONFIG.value, **ids))
        if not is_error:
            is_error = True
            error_message = unpriced_message
            error_code = ErrorCode.INVALID_CONFIG.value
    output = final_text.render()

    writer(
        events.cost_update(
            cost_usd=cost,
            duration_ms=elapsed_ms,
            # These APIs report no separate API timing, so wall time is reported.
            duration_api_ms=elapsed_ms,
            num_turns=totals["turns"],
            tokens_in=totals["in"],
            tokens_out=totals["out"],
            cache_read_tokens=totals["cache_read"],
            cache_creation_tokens=totals["cache_creation"],
            **ids,
        )
    )

    result = AgentOutput(
        agent_type=agent_type,
        is_error=is_error,
        error_message=error_message,
        error_code=error_code,
        output=output,
        cost_usd=cost,
        duration_ms=elapsed_ms,
        tools_called=tools_called,
        policy_denials=policy_denials,
        input_tokens=totals["in"],
        output_tokens=totals["out"],
        cache_read_tokens=totals["cache_read"],
        cache_creation_tokens=totals["cache_creation"],
        messages=captured if (not is_error or capped) else None,
        session_id=None,
        session_blob_path=None,
    )
    writer(
        events.agent_completed(
            is_error=is_error,
            error_code=error_code,
            error_message=error_message,
            output=scrub(output),
            cost_usd=cost,
            duration_ms=elapsed_ms,
            tools_called=tools_called,
            policy_denials=policy_denials,
            input_tokens=totals["in"],
            output_tokens=totals["out"],
            cache_read_tokens=totals["cache_read"],
            cache_creation_tokens=totals["cache_creation"],
            **ids,
        )
    )
    return result
