"""Minimal MCP stdio client for the direct-xAI executor (EMB-45 Phase C).

The QA agent drives Playwright through MCP. The Claude Code CLI spawned and spoke
MCP itself; the direct executor must do the same. This client implements exactly
the slice of MCP the executor needs against a local, per-sandbox, single-client
stdio server (playwright-mcp): spawn + initialize handshake, tools/list,
tools/call, teardown. Messages are JSON-RPC 2.0, newline-delimited UTF-8 per the
MCP stdio transport. Hand-rolled on stdlib only — the ``mcp`` python package is
not a declared dependency of the sandbox images (it is only a transitive pin of
claude-agent-sdk) and the full client library's lifecycle machinery doesn't fit
the executor's manual loop.

Teardown is the client's job: the sandbox idle watchdog only pkills the runner
process by name, so a spawned server (and its chromium descendants) would outlive
it. The subprocess gets its own process group and ``close()`` SIGKILLs the group.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

MCP_TOOL_PREFIX = "mcp__"

_PROTOCOL_VERSION = "2025-06-18"
# One JSON-RPC message per line; browser_snapshot lines run to hundreds of KB,
# far past asyncio's 64 KiB default StreamReader limit.
_STREAM_LIMIT = 16 * 1024 * 1024
_HANDSHAKE_TIMEOUT = 60.0
_CALL_TIMEOUT = 120.0
_CLOSE_GRACE = 3.0
# Same cap as the builtin tools (xai_tools._MAX_TOOL_RESULT_CHARS).
_MAX_RESULT_CHARS = 30_000


class McpClientError(RuntimeError):
    """Protocol or transport failure talking to an MCP server."""


def split_mcp_tool_name(name: str) -> tuple[str, str] | None:
    """``mcp__<server>__<tool>`` → ``(server, tool)``; None when malformed."""
    if not name.startswith(MCP_TOOL_PREFIX):
        return None
    server, sep, tool = name[len(MCP_TOOL_PREFIX) :].partition("__")
    if not sep or not server or not tool:
        return None
    return server, tool


def anthropic_tool_defs(
    server_name: str,
    mcp_tools: list[dict[str, Any]],
    allowed: list[str],
) -> list[dict[str, Any]]:
    """Anthropic tool schemas for the server's tools present in *allowed*.

    Names are prefixed ``mcp__<server>__<tool>`` to match the agent allowlist
    (the name gate is exact-match), and order follows *allowed* like
    :func:`embry0.agents.xai_tools.tool_defs` does for builtins.
    """
    by_name: dict[str, dict[str, Any]] = {}
    for tool in mcp_tools:
        tool_name = str(tool.get("name", ""))
        if not tool_name:
            continue
        full_name = f"{MCP_TOOL_PREFIX}{server_name}__{tool_name}"
        schema = dict(tool.get("inputSchema") or {"type": "object", "properties": {}})
        # xAI's Anthropic-compat validator rejects an input_schema whose
        # `required` key is absent ("/required: null is not of type 'array'").
        # Anthropic tolerates the omission; playwright-mcp omits it for tools
        # with no mandatory params. Normalize so both providers accept it.
        if not isinstance(schema.get("required"), list):
            schema["required"] = []
        by_name[full_name] = {
            "name": full_name,
            "description": str(tool.get("description", "")),
            "input_schema": schema,
        }
    return [by_name[name] for name in allowed if name in by_name]


def summarize_content(content: str | list[dict[str, Any]]) -> str:
    """Short text form of a tool result for event emission."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        btype = block.get("type", "")
        if btype == "text":
            parts.append(str(block.get("text", "")))
        elif btype == "image":
            source = block.get("source") or {}
            parts.append(f"[image {source.get('media_type', '')}, {len(str(source.get('data', '')))} chars base64]")
        else:
            parts.append(f"[{btype} block]")
    return "\n".join(parts)


def _truncate(text: str) -> str:
    if len(text) > _MAX_RESULT_CHARS:
        return text[:_MAX_RESULT_CHARS] + f"\n… [truncated {len(text) - _MAX_RESULT_CHARS} chars]"
    return text


def _to_anthropic_content(result: dict[str, Any]) -> str | list[dict[str, Any]]:
    """MCP CallToolResult content → Anthropic tool_result content.

    Text-only results collapse to a plain string; anything with images keeps a
    block list so screenshots reach the model (QA parity with the CLI path).
    Unsupported block types degrade to a text marker instead of failing.
    """
    blocks: list[dict[str, Any]] = []
    has_non_text = False
    for block in result.get("content") or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        if btype == "text":
            blocks.append({"type": "text", "text": _truncate(str(block.get("text", "")))})
        elif btype == "image":
            has_non_text = True
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": str(block.get("mimeType", "image/png")),
                        "data": str(block.get("data", "")),
                    },
                }
            )
        else:
            blocks.append({"type": "text", "text": f"[unsupported MCP content block: {btype!r}]"})
    if not blocks:
        return ""
    if has_non_text:
        return blocks
    return _truncate("\n".join(str(b.get("text", "")) for b in blocks))


class McpStdioClient:
    """One stdio MCP server: spawn, handshake, tools/list, tools/call, teardown."""

    def __init__(
        self,
        server_name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.server_name = server_name
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.pid: int | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 0

    async def start(self) -> None:
        """Spawn the server and run the MCP initialize handshake."""
        # Inherit the sandbox env (PLAYWRIGHT_MCP_STORAGE_STATE / _ISOLATED ride
        # it — EMB-40); per-server env from the config overlays it. stderr is
        # inherited so server diagnostics land in the runner's captured stderr
        # without corrupting the JSON-RPC stdout stream.
        self._proc = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            env={**os.environ, **self.env},
            start_new_session=True,  # own process group — see module docstring
            limit=_STREAM_LIMIT,
        )
        self.pid = self._proc.pid
        result = await self._request(
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "embry0-direct-xai", "version": "1.0"},
            },
            timeout=_HANDSHAKE_TIMEOUT,
        )
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        logger.info(
            "mcp_server_started",
            server=self.server_name,
            pid=self.pid,
            protocol_version=result.get("protocolVersion", ""),
        )

    async def list_tools(self) -> list[dict[str, Any]]:
        """All tools the server exposes (raw MCP shapes), following pagination."""
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"cursor": cursor} if cursor else {}
            result = await self._request("tools/list", params, timeout=_HANDSHAKE_TIMEOUT)
            tools.extend(t for t in result.get("tools") or [] if isinstance(t, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout: float = _CALL_TIMEOUT,
    ) -> tuple[str | list[dict[str, Any]], bool]:
        """tools/call → (anthropic tool_result content, is_error)."""
        result = await self._request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            timeout=timeout,
        )
        return _to_anthropic_content(result), bool(result.get("isError", False))

    async def close(self) -> None:
        """Terminate the server: close stdin, brief grace, then SIGKILL the group."""
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        if proc.returncode is None:
            with contextlib.suppress(OSError):
                if proc.stdin is not None:
                    proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), _CLOSE_GRACE)
            except TimeoutError:
                # Kill the whole group — chromium descendants included.
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
        logger.info("mcp_server_stopped", server=self.server_name, returncode=proc.returncode)

    # ---- JSON-RPC over newline-delimited stdio ----------------------------

    async def _send(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise McpClientError(f"mcp server {self.server_name!r} is not running")
        try:
            proc.stdin.write(json.dumps(payload).encode("utf-8") + b"\n")
            await proc.stdin.drain()
        except (OSError, ConnectionError) as exc:
            raise McpClientError(f"mcp server {self.server_name!r}: stdin write failed: {exc}") from exc

    async def _request(self, method: str, params: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        self._next_id += 1
        req_id = self._next_id
        await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        try:
            return await asyncio.wait_for(self._read_response(req_id, method), timeout)
        except TimeoutError as exc:
            raise McpClientError(f"mcp server {self.server_name!r}: {method} timed out after {timeout}s") from exc

    async def _read_response(self, req_id: int, method: str) -> dict[str, Any]:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise McpClientError(f"mcp server {self.server_name!r} is not running")
        while True:
            line = await proc.stdout.readline()
            if not line:
                raise McpClientError(
                    f"mcp server {self.server_name!r} exited (code {proc.returncode}) before responding to {method}"
                )
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("mcp_non_json_line", server=self.server_name, line=line[:200].decode(errors="replace"))
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("id") == req_id and ("result" in msg or "error" in msg):
                if "error" in msg:
                    err = msg["error"] or {}
                    raise McpClientError(
                        f"mcp server {self.server_name!r}: {method} error "
                        f"{err.get('code', '?')}: {err.get('message', '')}"
                    )
                result = msg.get("result")
                return result if isinstance(result, dict) else {}
            if "method" in msg and "id" in msg:
                # Server-initiated request (e.g. roots/list) — we declare no such
                # capability; refuse it so the server doesn't hang waiting.
                await self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "error": {"code": -32601, "message": "method not supported by this client"},
                    }
                )
                continue
            # Notification, or a stale response from a timed-out request — skip.


async def call_mcp_tool(
    clients: dict[str, McpStdioClient],
    name: str,
    arguments: dict[str, Any],
) -> tuple[str | list[dict[str, Any]], bool]:
    """Route a prefixed tool_use to its server. Never raises — errors come back
    as is_error results the model can react to, like ``execute_tool``."""
    parsed = split_mcp_tool_name(name)
    if parsed is None:
        return (f"malformed MCP tool name: {name!r}", True)
    server, tool = parsed
    client = clients.get(server)
    if client is None:
        return (f"no MCP server {server!r} is running for tool {name!r}", True)
    try:
        return await client.call_tool(tool, arguments)
    except Exception as exc:  # defensive — a server failure must not kill the loop
        return (f"{name} failed: {exc}", True)
