"""A line past the MCP stdio client's limit fails its request, and the next one reads its own answer (#466).

``McpStdioClient._read_response`` read the server's answers with
``StreamReader.readline()``, which raises ``ValueError`` for a line longer
than the reader's limit, not ``LimitOverrunError``, and outside the
``json.loads`` guard. So during ``start()`` such a line escaped as a bare
``ValueError`` rather than an ``McpClientError`` (and was not classified
``ERR_MCP_SERVER_FAILED``); during a tool call the model was told "Separator
is not found, and chunk exceed the limit". When the line's newline had not
arrived yet, ``readline()`` dropped what it had buffered and the rest of the
long line was read as a line of its own.

The client's limit is made small here so the long lines are a few KiB.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
from typing import Any

import pytest

from embry0.agents import mcp_client
from embry0.agents.mcp_client import McpClientError, McpStdioClient, call_mcp_tool
from embry0.safety.error_codes import ErrorCode, error_code_for_exception

LIMIT = 4096

_SERVER = textwrap.dedent(
    """
    import json, sys, time

    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    LONG = "x" * 20000
    for line in sys.stdin:
        msg = json.loads(line)
        method, mid = msg.get("method"), msg.get("id")
        if method == "initialize":
            if sys.argv[1] == "long-handshake":
                send({"jsonrpc": "2.0", "id": mid, "result": {"serverInfo": {"name": LONG}}})
            else:
                send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": "2025-06-18"}})
        elif method == "tools/call":
            name = msg["params"]["name"]
            if name == "long":
                # The long answer in two writes, its newline only in the second: the client reads
                # before the line has ended.
                answer = json.dumps({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": LONG}], "isError": False}})
                sys.stdout.write(answer[:10000])
                sys.stdout.flush()
                time.sleep(0.3)
                sys.stdout.write(answer[10000:] + "\\n")
                sys.stdout.flush()
            else:
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": "answered " + name}], "isError": False}})
    """
)


@pytest.fixture
def small_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_client, "_STREAM_LIMIT", LIMIT)


def _client(tmp_path: Any, mode: str) -> McpStdioClient:
    script = tmp_path / "long_lines_server.py"
    script.write_text(_SERVER)
    return McpStdioClient("playwright", sys.executable, [str(script), mode])


async def test_a_handshake_answer_past_the_limit_fails_start_as_an_mcp_client_error(tmp_path, small_limit) -> None:
    client = _client(tmp_path, "long-handshake")
    try:
        try:
            await client.start()
        except Exception as exc:  # noqa: BLE001 - the type is what is under test
            raised: BaseException | None = exc
        else:
            raised = None
    finally:
        await client.close()
    assert isinstance(raised, McpClientError), (
        f"a handshake answer past the limit escaped start() as {type(raised).__name__}: {raised}"
    )
    assert f"limit of {LIMIT:,} bytes" in str(raised) and "initialize" in str(raised), str(raised)
    assert error_code_for_exception(raised) == ErrorCode.MCP_SERVER_FAILED


async def test_a_tool_answer_past_the_limit_is_an_error_result_and_the_next_call_reads_its_own(
    tmp_path, small_limit
) -> None:
    client = _client(tmp_path, "short-handshake")
    await client.start()
    try:
        content, is_error = await call_mcp_tool({"playwright": client}, "mcp__playwright__long", {})
        assert is_error is True and f"limit of {LIMIT:,} bytes" in str(content), (
            f"the model is not told the answer was past the limit: {content!r}"
        )
        content, is_error = await asyncio.wait_for(client.call_tool("next", {}), 10)
        assert (content, is_error) == ("answered next", False), (
            f"the call after a long answer did not read its own answer: {content!r}"
        )
    finally:
        await client.close()


# ---- the line reader ----


def _read_line() -> Any:
    try:
        from embry0.agents.stream_lines import read_line
    except ImportError:
        pytest.fail("there is no line reader that reads past a line over the reader's limit")
    return read_line


async def test_a_line_past_the_limit_is_dropped_through_its_newline() -> None:
    read_line = _read_line()
    reader = asyncio.StreamReader(limit=16)
    reader.feed_data(b"x" * 40 + b"\nnext\n")
    reader.feed_eof()
    assert await read_line(reader) is None
    assert await read_line(reader) == b"next\n", "the read after a long line does not start at the next line"
    assert await read_line(reader) == b""


async def test_a_line_past_the_limit_whose_newline_comes_later_is_dropped_whole() -> None:
    read_line = _read_line()
    reader = asyncio.StreamReader(limit=16)
    reader.feed_data(b"x" * 40)

    async def rest() -> None:
        await asyncio.sleep(0.05)
        reader.feed_data(b"y" * 40 + b"\nnext\n")
        reader.feed_eof()

    feeding = asyncio.create_task(rest())
    assert await read_line(reader) is None
    assert await read_line(reader) == b"next\n", "the rest of the long line was read as a line of its own"
    await feeding


async def test_a_stream_that_ends_inside_a_long_line_or_without_a_newline() -> None:
    read_line = _read_line()
    reader = asyncio.StreamReader(limit=16)
    reader.feed_data(b"x" * 40)
    reader.feed_eof()
    assert await read_line(reader) is None and await read_line(reader) == b""
    reader = asyncio.StreamReader(limit=16)
    reader.feed_data(b"last")
    reader.feed_eof()
    assert await read_line(reader) == b"last" and await read_line(reader) == b""
