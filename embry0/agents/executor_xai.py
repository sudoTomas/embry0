"""DirectXaiExecutor — embry0-owned agentic tool-use loop for grok-4.5 (EMB-45).

Replaces the Claude Code CLI subprocess for xAI (grok) agents. The aim is grok driven
by the xAI API directly, authenticated by the SuperGrok OAuth bearer — not the CLI, not a
console XAI_API_KEY. Since the Claude Agent SDK *is* the CLI packaged as a library, that
means a new executor: a ``while stop_reason == "tool_use"`` loop against the Anthropic
Messages surface (``client.messages.create``), pointed at the xai-proxy which injects the
rotating SuperGrok bearer at egress (Phase A).

This executor re-supplies everything the CLI gave for free and must match its seams:
- Own tools (Read/Write/Edit/Bash/Glob/Grep) run in-sandbox — see :mod:`embry0.agents.xai_tools`.
- Ring-3 (tool-name + dangerous-Bash) and Ring-2 (filesystem deny globs) enforced per call
  via :func:`embry0.safety.policy.gate_tool_call` before dispatch. Fail-closed.
- Identical stdout event wire (agent_started/turn_start/text/tool_call/tool_result/thinking/
  cost_update/agent_completed/error/agent_ask_user) so AgentRunner is unchanged.
- Cost computed from token usage × provider pricing (xAI returns no total_cost_usd).
- Messages-list conversation capture for the resume fallback (no CLI JSONL session for grok).
- ask_user parity via the existing Bash-helper + embedded-event recovery.

MCP (Phase C): when the invocation declares ``mcp_servers``, each stdio server is
spawned via :mod:`embry0.agents.mcp_client`, its tools are exposed to the model as
``mcp__<server>__<tool>`` (filtered to the agent allowlist), and matching tool_use
blocks are routed to the server — gated by the same ``gate_tool_call`` first. This
is what lets grok QA agents drive Playwright.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, Any

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
    McpClientError,
    McpStdioClient,
    anthropic_tool_defs,
    call_mcp_tool,
    summarize_content,
)
from embry0.agents.output_text import FinalText
from embry0.agents.providers import price_usage, provider_for_model
from embry0.agents.xai_tools import BUILTIN_TOOL_NAMES, execute_tool, tool_defs
from embry0.execution import events
from embry0.execution.agent_runner import AgentOutput
from embry0.safety.error_codes import XAI_DENIAL_CODES, ErrorCode, classify_agent_error
from embry0.safety.policy import gate_tool_call
from embry0.safety.xai_denials import DESCRIPTIONS, classify_xai_denial
from embry0.sandbox.xai_token import read_xai_proxy_token as _read_proxy_token

if TYPE_CHECKING:
    from langchain_core.runnables.config import RunnableConfig

logger = structlog.get_logger(__name__)

_MAX_TOKENS_PER_TURN = 8192

_SYSTEM_PROMPT_BASE = """You are an autonomous software engineering agent operating inside \
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


def _make_client(base_url: str, auth_token: str) -> Any:
    """Construct the Anthropic async client pointed at the xai-proxy.

    Lazy-imports the SDK (absent from the orchestrator; present in the sandbox image)
    and is a module-level seam tests monkeypatch to inject a fake client.
    """
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(base_url=base_url, auth_token=auth_token, max_retries=2)


def _make_mcp_client(server_name: str, server_cfg: Any) -> McpStdioClient:
    """Build (not start) the stdio client for one ``mcp_servers`` entry.

    Module-level seam tests monkeypatch to inject a fake. Only stdio transport is
    supported — the per-sandbox server lifecycle is tied to this process.
    """
    cfg = server_cfg if isinstance(server_cfg, dict) else {}
    transport = str(cfg.get("type", "stdio"))
    if transport != "stdio":
        raise McpClientError(f"mcp server {server_name!r}: unsupported transport {transport!r} (only stdio)")
    command = str(cfg.get("command", ""))
    if not command:
        raise McpClientError(f"mcp server {server_name!r}: no command configured")
    return McpStdioClient(
        server_name,
        command,
        args=[str(a) for a in cfg.get("args") or []],
        env={str(k): str(v) for k, v in (cfg.get("env") or {}).items()},
    )


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Cost from provider pricing (xAI compat layer returns no total_cost_usd).

    None when the model has no price entry (#219): the price table is the only
    accounting this executor has, so an unpriced model is an error to record,
    never a silent $0. The resolver refuses such a model before the run; this
    is the last line if one gets here anyway.
    """
    prov = provider_for_model(model)
    rates = prov.pricing_usd_per_mtok.get(model) if prov is not None else None
    if not rates:
        return None
    in_rate, out_rate = rates
    return (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate


def _block_type(block: Any) -> str:
    return getattr(block, "type", "") or (block.get("type", "") if isinstance(block, dict) else "")


class DirectXaiExecutor:
    """Direct xAI Messages-API executor (grok). Implements the AgentExecutor protocol."""

    async def run(
        self,
        invocation: AgentInvocation,
        config: RunnableConfig | dict[str, Any] | None = None,
        *,
        resume_session_id: str | None = None,  # noqa: ARG002 — no CLI session for grok
        resume_messages: list[dict[str, Any]] | None = None,
    ) -> AgentOutput:
        # Lazy import so the orchestrator (which lacks the raw anthropic SDK) can
        # import this module; the SDK is only needed inside the sandbox at run time.
        writer = _resolve_writer(config)  # type: ignore[arg-type]
        started = time.time()
        agent_type = invocation.agent_type
        ids = _ids(invocation)
        scrub = make_scrubber()

        base_url = os.environ.get("EMBRY0_XAI_PROXY_URL", "")
        if not base_url:
            return _error_output(agent_type, "EMBRY0_XAI_PROXY_URL is not set in the sandbox environment")
        proxy_token = _read_proxy_token()
        if not proxy_token:
            return _error_output(agent_type, "xAI proxy bearer token is unavailable in the sandbox")

        allowed = [t for t in invocation.tools if t in BUILTIN_TOOL_NAMES]
        defs = tool_defs(allowed)

        # Phase C: spawn declared MCP stdio servers and expose their tools. Fail
        # loud — an agent whose config declares MCP (the QA agent) cannot do its
        # job without it, so a broken server is a job error, not a degraded run.
        mcp_clients: dict[str, McpStdioClient] = {}
        if invocation.mcp_servers:
            try:
                for server_name, server_cfg in invocation.mcp_servers.items():
                    mcp = _make_mcp_client(server_name, server_cfg)
                    mcp_clients[server_name] = mcp
                    await mcp.start()
                    defs.extend(anthropic_tool_defs(server_name, await mcp.list_tools(), invocation.tools))
            except Exception as exc:
                await _close_mcp_clients(mcp_clients)
                message = f"MCP server startup failed: {exc}"
                writer(events.error(message=message, code=ErrorCode.SANDBOX_INIT.value, **ids))
                return _error_output(agent_type, message)

        system = _SYSTEM_PROMPT_BASE
        if invocation.system_prompt:
            system = f"{system}\n\n{invocation.system_prompt}"

        prompt = invocation.prompt
        if invocation.system_context:
            prompt = f"{invocation.system_context}\n\n{prompt}"

        # Live conversation (full blocks) sent back each turn.
        messages: list[dict[str, Any]] = []
        if resume_messages:
            messages.extend(resume_messages)
        messages.append({"role": "user", "content": prompt})
        messages = _merge_consecutive_roles(messages)

        # Text-only capture for the Messages-API resume fallback (AgentOutput.messages).
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

        client = _make_client(base_url, proxy_token)

        writer(events.agent_started(model=invocation.model, max_turns=invocation.max_turns, **ids))

        async def execute() -> None:
            nonlocal capped
            turn = 0
            while turn < invocation.max_turns:
                turn += 1
                totals["turns"] = turn
                resp = await client.messages.create(
                    model=invocation.model,
                    max_tokens=_MAX_TOKENS_PER_TURN,
                    system=system,
                    tools=defs,
                    messages=messages,
                )
                usage = getattr(resp, "usage", None)
                if usage is not None:
                    totals["in"] += int(getattr(usage, "input_tokens", 0) or 0)
                    totals["out"] += int(getattr(usage, "output_tokens", 0) or 0)
                    totals["cache_read"] += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
                    totals["cache_creation"] += int(getattr(usage, "cache_creation_input_tokens", 0) or 0)

                writer(events.turn_start(turn_number=turn, model=invocation.model, **ids))
                if usage is not None:
                    turn_in = int(getattr(usage, "input_tokens", 0) or 0)
                    turn_out = int(getattr(usage, "output_tokens", 0) or 0)
                    turn_cr = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
                    turn_cc = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
                    writer(
                        events.turn_usage(
                            input_tokens=turn_in,
                            output_tokens=turn_out,
                            cache_read_tokens=turn_cr,
                            cache_creation_tokens=turn_cc,
                            cost_usd=price_usage(
                                invocation.model,
                                input_tokens=turn_in,
                                output_tokens=turn_out,
                                cache_read_tokens=turn_cr,
                                cache_creation_tokens=turn_cc,
                            ),
                            model=invocation.model,
                            **ids,
                        )
                    )

                content = list(getattr(resp, "content", []) or [])
                assistant_text = ""
                tool_uses: list[Any] = []
                for block in content:
                    btype = _block_type(block)
                    if btype == "text":
                        text = str(getattr(block, "text", ""))
                        assistant_text += text
                        writer(events.text(text=scrub(text), **ids))
                    elif btype == "thinking":
                        writer(events.thinking(text=scrub(str(getattr(block, "thinking", ""))), **ids))
                    elif btype == "tool_use":
                        tool_uses.append(block)

                # Append the assistant turn (full blocks) so the next request has context.
                messages.append({"role": "assistant", "content": content})
                if assistant_text:
                    captured.append({"role": "assistant", "content": assistant_text})
                # #329: one entry per message; joined with a blank line at the end.
                final_text.add_message(assistant_text)

                if getattr(resp, "stop_reason", None) != "tool_use" or not tool_uses:
                    return  # end_turn / max_tokens / no tools requested → done

                # Execute each requested tool, gating first; collect tool_result blocks.
                results: list[dict[str, Any]] = []
                for tu in tool_uses:
                    name = str(getattr(tu, "name", ""))
                    tool_id = str(getattr(tu, "id", ""))
                    raw_input = getattr(tu, "input", {})
                    tool_input = raw_input if isinstance(raw_input, dict) else {}
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

                    verdict = gate_tool_call(policy, name, tool_input, cwd=cwd)
                    if not verdict.allowed:
                        # Keyed by the policy's reason, as the Claude path keys it
                        # (executor.py _evaluate_hook), so both paths report one shape.
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
                        # The Anthropic request block (keyed by tool_use_id) sent back to the
                        # model, not an embry0 event; the event goes out through events.tool_result.
                        results.append(
                            {"type": "tool_result", "tool_use_id": tool_id, "content": verdict.reason, "is_error": True}
                        )
                        writer(events.tool_result(tool_id=tool_id, content=verdict.reason or "", is_error=True, **ids))
                        continue

                    result_content: str | list[dict[str, Any]]
                    if name.startswith(MCP_TOOL_PREFIX):
                        result_content, tool_err = await call_mcp_tool(mcp_clients, name, tool_input)
                        result_text = summarize_content(result_content)
                    else:
                        result_text, tool_err = execute_tool(name, tool_input, cwd=cwd, policy=policy)
                        result_content = result_text
                        # ask_user parity: the Bash ask_user helper prints its event to stdout,
                        # which lands in this tool result — recover + re-emit it (EMB-44).
                        if name == "Bash":
                            for embedded in _extract_embedded_ask_user_events(result_text):
                                writer({**embedded, **ids})
                    results.append(
                        {"type": "tool_result", "tool_use_id": tool_id, "content": result_content, "is_error": tool_err}
                    )
                    writer(events.tool_result(tool_id=tool_id, content=scrub(result_text), is_error=tool_err, **ids))

                messages.append({"role": "user", "content": results})
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
            error_code, error_message = _xai_failure(exc)
            writer(events.error(message=error_message, code=error_code, **ids))
        finally:
            await client.close()
            await _close_mcp_clients(mcp_clients)

        # RAV-1260: a turn-cap death is an error, and the one error whose conversation is kept —
        # triage/review resume it once to decide now, the developer node commits the unfinished
        # work. Both key on is_error + is_max_turns_error(), as the CLI executor reports it.
        if capped and not is_error:
            is_error = True
            error_message = f"reached max_turns ({invocation.max_turns})"
            error_code = ErrorCode.AGENT_MAX_TURNS.value

        elapsed_ms = int((time.time() - started) * 1000)
        priced = _compute_cost(invocation.model, totals["in"], totals["out"])
        cost = priced if priced is not None else 0.0
        if priced is None:
            unpriced_message = (
                f"cost unknown: model {invocation.model!r} has no price entry in "
                "embry0/agents/providers.py, so this run's "
                f"{totals['in']} input and {totals['out']} output tokens are unmetered"
            )
            logger.error(
                "xai_cost_unpriced_model",
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
                # The xAI path has no separate API timing, so wall time is reported.
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


async def _close_mcp_clients(clients: dict[str, McpStdioClient]) -> None:
    """Best-effort teardown — a close failure must not mask the run's outcome."""
    for server_name, mcp in clients.items():
        try:
            await mcp.close()
        except Exception:
            logger.warning("mcp_client_close_failed", server=server_name, exc_info=True)


def _xai_failure(exc: BaseException) -> tuple[str | None, str]:
    """(code, message) for an xAI call that raised.

    When the error names an xAI 403 condition (embry0/safety/xai_denials.py), the
    message leads with what that means, then keeps xAI's own words.
    """
    message = str(exc)
    code = _xai_exception_code(exc)
    if code is None:
        classified = classify_agent_error(message)
        code = classified.value if classified else None
    denial = classify_xai_denial(message)
    if denial is not None and code == XAI_DENIAL_CODES[denial].value:
        message = f"{DESCRIPTIONS[denial]} {message}"
    return code, message


def _xai_exception_code(exc: BaseException) -> str | None:
    """Typed anthropic SDK failures. Anything else is left for the text classifier."""
    try:
        import anthropic
    except ImportError:
        return None
    rate = getattr(anthropic, "RateLimitError", None)
    if isinstance(rate, type) and isinstance(exc, rate):
        return ErrorCode.PROVIDER_RATE_LIMITED.value
    # xAI answers a spent spending cap, a missing subscription and a stale token
    # all with 403; the body says which. Only an unrecognised 403 is refused credentials.
    permission = getattr(anthropic, "PermissionDeniedError", None)
    if isinstance(permission, type) and isinstance(exc, permission):
        denial = classify_xai_denial(str(exc))
        if denial is not None:
            return XAI_DENIAL_CODES[denial].value
    auth = tuple(
        cls
        for name in ("AuthenticationError", "PermissionDeniedError")
        if isinstance(cls := getattr(anthropic, name, None), type)
    )
    if auth and isinstance(exc, auth):
        return ErrorCode.AUTH_REJECTED.value
    return None


def _merge_consecutive_roles(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join back-to-back text messages of one role into one, so roles alternate.

    The resume capture (``captured``) holds one assistant entry per turn, so a replayed
    transcript has assistant messages back to back. Anthropic merges consecutive turns server
    side; xAI's compat layer does not document doing so. Block-content messages are left as
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


def _error_output(agent_type: str, message: str) -> AgentOutput:
    return AgentOutput(agent_type=agent_type, is_error=True, error_message=message)
