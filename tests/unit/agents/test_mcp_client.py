"""Tests for the MCP stdio client (EMB-45 Phase C).

Drives the real McpStdioClient against a fake MCP server subprocess — a small
python script speaking newline-delimited JSON-RPC 2.0 on stdin/stdout, shaped
like playwright-mcp (initialize handshake, tools/list, tools/call, interleaved
notifications). No network, no Playwright.
"""

from __future__ import annotations

import os
import sys
import textwrap

import pytest

from embry0.agents.mcp_client import (
    McpClientError,
    McpStdioClient,
    anthropic_tool_defs,
    call_mcp_tool,
    split_mcp_tool_name,
    summarize_content,
)

_FAKE_SERVER = textwrap.dedent(
    """
    import json, os, sys

    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    TOOLS = [
        {
            "name": "browser_navigate",
            "description": "Navigate to a URL",
            "inputSchema": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
        {"name": "browser_take_screenshot", "description": "Screenshot", "inputSchema": {"type": "object"}},
    ]

    for line in sys.stdin:
        msg = json.loads(line)
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": msg["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-playwright", "version": "0.0.1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name = msg["params"]["name"]
            args = msg["params"].get("arguments") or {}
            # Interleaved notification — the client must skip it.
            send({"jsonrpc": "2.0", "method": "notifications/message",
                  "params": {"level": "info", "data": "noise"}})
            if name == "browser_navigate":
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": "Navigated to " + args.get("url", "")}],
                    "isError": False}})
            elif name == "browser_take_screenshot":
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [
                        {"type": "text", "text": "screenshot taken"},
                        {"type": "image", "data": "aWJhc2U2NA==", "mimeType": "image/png"},
                    ],
                    "isError": False}})
            elif name == "boom":
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": "boom happened"}], "isError": True}})
            elif name == "echo_env":
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": os.environ.get("FAKE_ENV_PROBE", "")}],
                    "isError": False}})
            else:
                send({"jsonrpc": "2.0", "id": mid,
                      "error": {"code": -32602, "message": "unknown tool " + name}})
    """
)


@pytest.fixture
def server_script(tmp_path):
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(_FAKE_SERVER)
    return script


@pytest.fixture
async def client(server_script):
    c = McpStdioClient("playwright", sys.executable, [str(server_script)])
    await c.start()
    yield c
    await c.close()


async def test_handshake_and_list_tools(client):
    tools = await client.list_tools()
    assert [t["name"] for t in tools] == ["browser_navigate", "browser_take_screenshot"]
    assert tools[0]["inputSchema"]["required"] == ["url"]


async def test_call_tool_text_result(client):
    content, is_error = await client.call_tool("browser_navigate", {"url": "http://app:3000"})
    assert content == "Navigated to http://app:3000"
    assert is_error is False


async def test_call_tool_server_is_error(client):
    content, is_error = await client.call_tool("boom", {})
    assert is_error is True
    assert "boom happened" in content


async def test_call_tool_image_blocks_pass_through(client):
    content, is_error = await client.call_tool("browser_take_screenshot", {})
    assert is_error is False
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "screenshot taken"}
    assert content[1]["type"] == "image"
    assert content[1]["source"] == {"type": "base64", "media_type": "image/png", "data": "aWJhc2U2NA=="}


async def test_jsonrpc_error_raises(client):
    with pytest.raises(McpClientError, match="unknown tool nope"):
        await client.call_tool("nope", {})


async def test_server_env_overlay(server_script):
    c = McpStdioClient("playwright", sys.executable, [str(server_script)], env={"FAKE_ENV_PROBE": "state.json"})
    await c.start()
    try:
        content, _ = await c.call_tool("echo_env", {})
        assert content == "state.json"
    finally:
        await c.close()


async def test_sandbox_env_inherited(server_script, monkeypatch):
    # EMB-40: PLAYWRIGHT_MCP_STORAGE_STATE etc. ride the process env — the
    # client must inherit it, not spawn with a scrubbed environment.
    monkeypatch.setenv("FAKE_ENV_PROBE", "inherited.json")
    c = McpStdioClient("playwright", sys.executable, [str(server_script)])
    await c.start()
    try:
        content, _ = await c.call_tool("echo_env", {})
        assert content == "inherited.json"
    finally:
        await c.close()


async def test_close_terminates_process(server_script):
    c = McpStdioClient("playwright", sys.executable, [str(server_script)])
    await c.start()
    pid = c.pid
    assert pid is not None
    await c.close()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_spawn_failure_raises(tmp_path):
    c = McpStdioClient("playwright", str(tmp_path / "no-such-binary"))
    with pytest.raises(OSError):
        await c.start()
    await c.close()  # idempotent — must not raise


async def test_call_mcp_tool_routes_and_shields(client):
    clients = {"playwright": client}
    content, is_error = await call_mcp_tool(clients, "mcp__playwright__browser_navigate", {"url": "http://x"})
    assert content == "Navigated to http://x" and is_error is False
    # JSON-RPC error surfaces as an is_error result, not an exception.
    content, is_error = await call_mcp_tool(clients, "mcp__playwright__nope", {})
    assert is_error is True and "unknown tool nope" in content
    # Unknown server and malformed names are is_error results too.
    content, is_error = await call_mcp_tool(clients, "mcp__other__x", {})
    assert is_error is True and "no MCP server" in content
    content, is_error = await call_mcp_tool(clients, "not_an_mcp_name", {})
    assert is_error is True and "malformed" in content


# ---- pure helpers ---------------------------------------------------------


def test_split_mcp_tool_name():
    assert split_mcp_tool_name("mcp__playwright__browser_navigate") == ("playwright", "browser_navigate")
    assert split_mcp_tool_name("mcp__s__t__extra") == ("s", "t__extra")
    assert split_mcp_tool_name("Read") is None
    assert split_mcp_tool_name("mcp__noseparator") is None
    assert split_mcp_tool_name("mcp____tool") is None


def test_anthropic_tool_defs_filters_and_orders_by_allowlist():
    mcp_tools = [
        {"name": "browser_run_code_unsafe", "description": "evil", "inputSchema": {"type": "object"}},
        {"name": "browser_click", "description": "click", "inputSchema": {"type": "object"}},
        {"name": "browser_navigate", "description": "nav", "inputSchema": {"type": "object"}},
    ]
    allowed = ["Read", "mcp__playwright__browser_navigate", "mcp__playwright__browser_click"]
    defs = anthropic_tool_defs("playwright", mcp_tools, allowed)
    assert [d["name"] for d in defs] == ["mcp__playwright__browser_navigate", "mcp__playwright__browser_click"]
    assert all(d["input_schema"] == {"type": "object"} for d in defs)
    # browser_run_code_unsafe is not in the allowlist → never exposed (EMB-37).


def test_anthropic_tool_defs_defaults_missing_schema():
    defs = anthropic_tool_defs("s", [{"name": "t"}], ["mcp__s__t"])
    assert defs[0]["input_schema"] == {"type": "object", "properties": {}}


def test_summarize_content():
    assert summarize_content("plain") == "plain"
    blocks = [
        {"type": "text", "text": "hello"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abcd"}},
    ]
    summary = summarize_content(blocks)
    assert "hello" in summary and "image/png" in summary
