"""DirectXaiExecutor — embry0-owned agentic tool-use loop for grok-4.5 (EMB-45).

Replaces the Claude Code CLI subprocess for xAI (grok) agents. Diego wants grok driven
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

from embry0.agents.executor import (
    _extract_embedded_ask_user_events,
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
from embry0.agents.providers import provider_for_model
from embry0.agents.xai_tools import BUILTIN_TOOL_NAMES, execute_tool, tool_defs
from embry0.execution.agent_runner import AgentOutput
from embry0.safety.policy import gate_tool_call
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


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Cost from provider pricing (xAI compat layer returns no total_cost_usd)."""
    prov = provider_for_model(model)
    if prov is None:
        return 0.0
    rates = prov.pricing_usd_per_mtok.get(model)
    if not rates:
        return 0.0
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
                writer({"type": "error", "error": message})
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

        # Text-only capture for the Messages-API resume fallback (AgentOutput.messages).
        captured: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

        cwd = str(_workspace_root())
        policy = invocation.safety_policy

        totals = {"in": 0, "out": 0, "cache_read": 0, "cache_creation": 0}
        tools_called: dict[str, int] = {}
        output_text = ""
        is_error = False
        error_message = ""

        client = _make_client(base_url, proxy_token)

        writer({"type": "agent_started", "agent": agent_type})

        async def execute() -> None:
            nonlocal output_text
            turn = 0
            while turn < invocation.max_turns:
                turn += 1
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

                writer({"type": "turn_start", "turn_number": turn, "model": invocation.model, "node": agent_type})

                content = list(getattr(resp, "content", []) or [])
                assistant_text = ""
                tool_uses: list[Any] = []
                for block in content:
                    btype = _block_type(block)
                    if btype == "text":
                        text = str(getattr(block, "text", ""))
                        output_text += text
                        assistant_text += text
                        writer({"type": "text", "text": text[:2000], "node": agent_type})
                    elif btype == "thinking":
                        writer(
                            {"type": "thinking", "text": str(getattr(block, "thinking", ""))[:3000], "node": agent_type}
                        )
                    elif btype == "tool_use":
                        tool_uses.append(block)

                # Append the assistant turn (full blocks) so the next request has context.
                messages.append({"role": "assistant", "content": content})
                if assistant_text:
                    captured.append({"role": "assistant", "content": assistant_text})

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
                        {
                            "type": "tool_call",
                            "tool_name": name,
                            "tool_id": tool_id,
                            "input": _summarize_tool_input(name, tool_input),
                            "tool_input": tool_input,
                            "node": agent_type,
                        }
                    )

                    verdict = gate_tool_call(policy, name, tool_input, cwd=cwd)
                    if not verdict.allowed:
                        writer({"type": "error", "error": verdict.reason, "tool_name": name})
                        results.append(
                            {"type": "tool_result", "tool_use_id": tool_id, "content": verdict.reason, "is_error": True}
                        )
                        writer(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": verdict.reason[:1000],
                                "is_error": True,
                                "node": agent_type,
                            }
                        )
                        continue

                    result_content: str | list[dict[str, Any]]
                    if name.startswith(MCP_TOOL_PREFIX):
                        result_content, tool_err = await call_mcp_tool(mcp_clients, name, tool_input)
                        result_text = summarize_content(result_content)
                    else:
                        result_text, tool_err = execute_tool(name, tool_input, cwd=cwd)
                        result_content = result_text
                        # ask_user parity: the Bash ask_user helper prints its event to stdout,
                        # which lands in this tool result — recover + re-emit it (EMB-44).
                        if name == "Bash":
                            for embedded in _extract_embedded_ask_user_events(result_text):
                                embedded["node"] = agent_type
                                writer(embedded)
                    results.append(
                        {"type": "tool_result", "tool_use_id": tool_id, "content": result_content, "is_error": tool_err}
                    )
                    writer(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": result_text[:1000],
                            "is_error": tool_err,
                            "node": agent_type,
                        }
                    )

                messages.append({"role": "user", "content": results})
            # Loop exhausted max_turns without a natural stop.
            writer({"type": "error", "error": f"reached max_turns ({invocation.max_turns})"})

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
        finally:
            await client.close()
            await _close_mcp_clients(mcp_clients)

        elapsed_ms = int((time.time() - started) * 1000)
        cost = _compute_cost(invocation.model, totals["in"], totals["out"])
        output = output_text[-10000:] if len(output_text) > 10000 else output_text

        writer(
            {
                "type": "cost_update",
                "cost_usd": cost,
                "duration_ms": elapsed_ms,
                "num_turns": len(tools_called),
                "tokens_in": totals["in"],
                "tokens_out": totals["out"],
                "cache_read_tokens": totals["cache_read"],
                "cache_creation_tokens": totals["cache_creation"],
                "node": agent_type,
            }
        )

        result = AgentOutput(
            agent_type=agent_type,
            is_error=is_error,
            error_message=error_message,
            output=output,
            cost_usd=cost,
            duration_ms=elapsed_ms,
            tools_called=tools_called,
            input_tokens=totals["in"],
            output_tokens=totals["out"],
            cache_read_tokens=totals["cache_read"],
            cache_creation_tokens=totals["cache_creation"],
            messages=None if is_error else captured,
            session_id=None,
            session_blob_path=None,
        )
        writer(
            {
                "type": "agent_completed",
                "result": {
                    "agent_type": agent_type,
                    "is_error": is_error,
                    "error_message": error_message,
                    "output": output,
                    "cost_usd": cost,
                    "duration_ms": elapsed_ms,
                    "tools_called": tools_called,
                    "input_tokens": totals["in"],
                    "output_tokens": totals["out"],
                    "cache_read_tokens": totals["cache_read"],
                    "cache_creation_tokens": totals["cache_creation"],
                },
                "cost_usd": cost,
                "duration_ms": elapsed_ms,
                "tools_called": tools_called,
            }
        )
        return result


async def _close_mcp_clients(clients: dict[str, McpStdioClient]) -> None:
    """Best-effort teardown — a close failure must not mask the run's outcome."""
    for server_name, mcp in clients.items():
        try:
            await mcp.close()
        except Exception:
            logger.warning("mcp_client_close_failed", server=server_name, exc_info=True)


def _error_output(agent_type: str, message: str) -> AgentOutput:
    return AgentOutput(agent_type=agent_type, is_error=True, error_message=message)
