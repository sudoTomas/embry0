"""DirectXaiExecutor — embry0-owned agentic tool-use loop for grok (EMB-45).

Replaces the Claude Code CLI subprocess for xAI (grok) agents. The aim is grok driven
by the xAI API directly, authenticated by the SuperGrok OAuth bearer — not the CLI, not a
console XAI_API_KEY. Since the Claude Agent SDK *is* the CLI packaged as a library, that
means a new executor: a ``while stop_reason == "tool_use"`` loop against the Anthropic
Messages surface (``client.messages.create``), pointed at the xai-proxy which injects the
rotating SuperGrok bearer at egress (Phase A).

The loop itself — builtin and MCP tools, ``gate_tool_call`` before every dispatch, the
stdout event wire, token-priced cost, the resume capture, ask_user recovery and the turn
cap — is :func:`embry0.agents._tool_loop.run_tool_loop`, shared with the
OpenAI-compatible executor. This module supplies the Anthropic Messages wire and the
xAI-specific setup and failure classification.

MCP (Phase C): when the invocation declares ``mcp_servers``, each stdio server is
spawned via :mod:`embry0.agents.mcp_client`, its tools are exposed to the model as
``mcp__<server>__<tool>`` (filtered to the agent allowlist), and matching tool_use
blocks are routed to the server — gated by the same ``gate_tool_call`` first. This
is what lets grok QA agents drive Playwright.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from embry0.agents._tool_loop import ToolCall, ToolResult, Turn, Usage, error_output, run_tool_loop
from embry0.agents.invocation import AgentInvocation
from embry0.agents.mcp_client import McpClientError, McpStdioClient
from embry0.agents.providers import price_usage
from embry0.agents.xai_tools import execute_tool
from embry0.execution.agent_runner import AgentOutput
from embry0.safety.error_codes import XAI_DENIAL_CODES, ErrorCode, classify_agent_error
from embry0.safety.xai_denials import DESCRIPTIONS, classify_xai_denial
from embry0.sandbox.xai_token import read_xai_proxy_token as _read_proxy_token

if TYPE_CHECKING:
    from langchain_core.runnables.config import RunnableConfig

_MAX_TOKENS_PER_TURN = 8192


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


def _compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float | None:
    """Cost from provider pricing (xAI compat layer returns no total_cost_usd).

    None when the model has no price entry (#219): the price table is the only
    accounting this executor has, so an unpriced model is an error to record,
    never a silent $0. Prices cache reads and writes with the same factors as
    the SDK path, so cost_update agrees with the summed turn_usage.
    """
    return price_usage(
        model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
    )


def _block_type(block: Any) -> str:
    return getattr(block, "type", "") or (block.get("type", "") if isinstance(block, dict) else "")


class _AnthropicMessagesWire:
    """The Anthropic Messages API (``client.messages.create``), as xAI's compat layer serves it."""

    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self._model = model

    def tool_defs(self, defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return defs

    async def turn(self, *, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> Turn:
        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=_MAX_TOKENS_PER_TURN,
            system=system,
            tools=tools,
            messages=messages,
        )
        raw_usage = getattr(resp, "usage", None)
        usage = (
            Usage(
                input_tokens=int(getattr(raw_usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(raw_usage, "output_tokens", 0) or 0),
                cache_read_tokens=int(getattr(raw_usage, "cache_read_input_tokens", 0) or 0),
                cache_creation_tokens=int(getattr(raw_usage, "cache_creation_input_tokens", 0) or 0),
            )
            if raw_usage is not None
            else None
        )
        content = list(getattr(resp, "content", []) or [])
        parts: list[tuple[str, str]] = []
        tool_calls: list[ToolCall] = []
        for block in content:
            btype = _block_type(block)
            if btype == "text":
                parts.append(("text", str(getattr(block, "text", ""))))
            elif btype == "thinking":
                parts.append(("thinking", str(getattr(block, "thinking", ""))))
            elif btype == "tool_use":
                raw_input = getattr(block, "input", {})
                tool_calls.append(
                    ToolCall(
                        id=str(getattr(block, "id", "")),
                        name=str(getattr(block, "name", "")),
                        input=raw_input if isinstance(raw_input, dict) else {},
                    )
                )
        return Turn(
            parts=parts,
            tool_calls=tool_calls,
            wants_tools=getattr(resp, "stop_reason", None) == "tool_use",
            usage=usage,
            # Full blocks, so the next request has the tool_use blocks its results answer.
            assistant_message={"role": "assistant", "content": content},
        )

    def tool_result_messages(self, results: list[ToolResult]) -> list[dict[str, Any]]:
        # Anthropic request blocks (keyed by tool_use_id), all in one user message.
        return [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": r.tool_id, "content": r.content, "is_error": r.is_error}
                    for r in results
                ],
            }
        ]

    def failure(self, exc: BaseException) -> tuple[str | None, str]:
        return _xai_failure(exc)

    async def close(self) -> None:
        await self._client.close()


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
        base_url = os.environ.get("EMBRY0_XAI_PROXY_URL", "")
        if not base_url:
            return error_output(invocation.agent_type, "EMBRY0_XAI_PROXY_URL is not set in the sandbox environment")
        proxy_token = _read_proxy_token()
        if not proxy_token:
            return error_output(invocation.agent_type, "xAI proxy bearer token is unavailable in the sandbox")

        # The seams are read here, from this module, so a test that replaces one
        # of them on this module replaces it for the run.
        return await run_tool_loop(
            invocation,
            config,
            make_wire=lambda: _AnthropicMessagesWire(_make_client(base_url, proxy_token), invocation.model),
            execute_builtin=execute_tool,
            make_mcp_client=_make_mcp_client,
            compute_cost=_compute_cost,
            resume_messages=resume_messages,
            unpriced_log_event="xai_cost_unpriced_model",
        )


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
