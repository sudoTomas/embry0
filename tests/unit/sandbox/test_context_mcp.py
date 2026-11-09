"""Tests for the in-sandbox logs MCP server (RAV-1140).

All tests drive the pure handlers (handle_message / run_query) directly — no
subprocess, no network. The proxy HTTP layer is stubbed via urllib monkeypatch.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from embry0.sandbox import context_mcp


@pytest.fixture
def provisioned(monkeypatch):
    monkeypatch.setenv("EMBRY0_CONTEXT_PROXY_URL", "http://context-proxy:9107")
    monkeypatch.setenv("EMBRY0_CONTEXT_PROXY_TOKEN", "tok-" + "x" * 40)


def _stub_urlopen(monkeypatch, payload: dict | None = None, error: Exception | None = None):
    captured: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload or {}).encode()

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["timeout"] = timeout
        if error is not None:
            raise error
        return _Resp()

    monkeypatch.setattr(context_mcp.urllib.request, "urlopen", fake_urlopen)
    return captured


def _http_error(code: int, body: dict | None = None) -> urllib.error.HTTPError:
    payload = json.dumps(body or {}).encode()
    return urllib.error.HTTPError(
        url="http://context-proxy:9107/api/v1/logs/query",
        code=code,
        msg="err",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(payload),
    )


# --- protocol ---


def test_initialize_echoes_requested_version():
    reply = context_mcp.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}
    )
    assert reply["result"]["protocolVersion"] == "2025-03-26"
    assert reply["result"]["serverInfo"]["name"] == "context"


def test_initialize_defaults_version_when_absent():
    reply = context_mcp.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert reply["result"]["protocolVersion"] == context_mcp._PROTOCOL_VERSION


def test_notification_gets_no_reply():
    assert context_mcp.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_ping():
    reply = context_mcp.handle_message({"jsonrpc": "2.0", "id": 7, "method": "ping"})
    assert reply == {"jsonrpc": "2.0", "id": 7, "result": {}}


def test_tools_list_declares_logs_query():
    reply = context_mcp.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = reply["result"]["tools"]
    assert [t["name"] for t in tools] == ["logs_query"]
    assert tools[0]["inputSchema"]["required"] == ["container"]


def test_unknown_method_is_json_rpc_error():
    reply = context_mcp.handle_message({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
    assert reply["error"]["code"] == -32601


def test_unknown_tool_is_error_result():
    reply = context_mcp.handle_message(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "bash", "arguments": {}}}
    )
    assert reply["result"]["isError"] is True
    assert "unknown tool" in reply["result"]["content"][0]["text"]


def test_handler_exception_still_replies(monkeypatch):
    monkeypatch.setattr(context_mcp, "run_query", lambda args: (_ for _ in ()).throw(RuntimeError("boom")))
    reply = context_mcp.handle_message(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "logs_query", "arguments": {}}}
    )
    assert reply["result"]["isError"] is True
    assert "boom" in reply["result"]["content"][0]["text"]


# --- run_query: provisioning + request shape ---


def test_unprovisioned_is_briefing_only_message(monkeypatch):
    monkeypatch.delenv("EMBRY0_CONTEXT_PROXY_URL", raising=False)
    monkeypatch.delenv("EMBRY0_CONTEXT_PROXY_TOKEN", raising=False)
    monkeypatch.setenv("EMBRY0_CONTEXT_PROXY_TOKEN_PATH", "/nonexistent")
    text, is_error = context_mcp.run_query({"container": "x"})
    assert is_error and "briefing pack" in text


def test_missing_container_is_error(provisioned):
    text, is_error = context_mcp.run_query({})
    assert is_error and "container" in text


def test_request_carries_bearer_and_clamped_params(provisioned, monkeypatch):
    captured = _stub_urlopen(monkeypatch, payload={"query": "q", "count": 0, "lines": []})
    text, is_error = context_mcp.run_query(
        {"container": "acme-backend", "since_minutes": 9999, "limit": 9999, "search": "error"}
    )
    assert not is_error
    assert captured["headers"].get("Authorization") == "Bearer tok-" + "x" * 40
    assert "since_minutes=240" in captured["url"]
    assert "limit=200" in captured["url"]
    assert "search=error" in captured["url"]
    assert captured["timeout"] == context_mcp._REQUEST_TIMEOUT
    assert "clamped: since_minutes=240, limit=200" in text


def test_result_formatting_and_iso_ts(provisioned, monkeypatch):
    _stub_urlopen(
        monkeypatch,
        payload={
            "query": "{container_name=~...}",
            "count": 1,
            "window_start": "s",
            "window_end": "e",
            "lines": [{"ts": "1753900000000000000", "container": "acme-backend", "line": "ERROR boom"}],
        },
    )
    text, is_error = context_mcp.run_query({"container": "acme-backend"})
    assert not is_error
    assert "count=1" in text
    assert "2025-07-30T18:26:40Z" in text  # ns epoch rendered as ISO UTC
    assert "ERROR boom" in text


def test_empty_result_says_so(provisioned, monkeypatch):
    _stub_urlopen(monkeypatch, payload={"query": "q", "count": 0, "lines": []})
    text, is_error = context_mcp.run_query({"container": "acme-backend"})
    assert not is_error and "no matching lines" in text


def test_long_result_is_truncated(provisioned, monkeypatch):
    lines = [{"ts": "0", "container": "c", "line": "x" * 500} for _ in range(200)]
    _stub_urlopen(monkeypatch, payload={"query": "q", "count": 200, "lines": lines})
    text, _ = context_mcp.run_query({"container": "acme-backend"})
    assert len(text) < context_mcp._MAX_RESULT_CHARS + 100
    assert "[truncated" in text


# --- run_query: failure modes (decision-5 strings) ---


def test_401_not_enrolled(provisioned, monkeypatch):
    _stub_urlopen(monkeypatch, error=_http_error(401, {"error": "unauthorized"}))
    text, is_error = context_mcp.run_query({"container": "x"})
    assert is_error and "not enrolled" in text


def test_400_detail_verbatim(provisioned, monkeypatch):
    _stub_urlopen(monkeypatch, error=_http_error(400, {"error": "container 'x' is not queryable"}))
    text, is_error = context_mcp.run_query({"container": "x"})
    assert is_error and "not queryable" in text


def test_429_quota_message(provisioned, monkeypatch):
    _stub_urlopen(monkeypatch, error=_http_error(429, {"error": "log query quota exhausted (30 per job)"}))
    text, is_error = context_mcp.run_query({"container": "x"})
    assert is_error and "quota exhausted" in text and "conclude from evidence" in text


def test_502_backend_unavailable(provisioned, monkeypatch):
    _stub_urlopen(monkeypatch, error=_http_error(502, {"detail": "Loki query failed: boom"}))
    text, is_error = context_mcp.run_query({"container": "x"})
    assert is_error and "log backend unavailable" in text and "Loki" in text


def test_connection_refused_is_unreachable(provisioned, monkeypatch):
    _stub_urlopen(monkeypatch, error=urllib.error.URLError("connection refused"))
    text, is_error = context_mcp.run_query({"container": "x"})
    assert is_error and "unreachable" in text


# --- stdio loop ---


def test_main_loop_round_trip(provisioned, monkeypatch, capsys):
    messages = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        "not json at all",
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    ]
    monkeypatch.setattr(context_mcp.sys, "stdin", io.StringIO("\n".join(messages) + "\n"))
    context_mcp.main()
    out = capsys.readouterr()
    replies = [json.loads(line) for line in out.out.strip().splitlines()]
    assert [r["id"] for r in replies] == [1, 2]
    assert "unparseable" in out.err


def test_main_loop_skips_lines_python_cannot_decode(provisioned, monkeypatch, capsys):
    """A message nested past the decoder's recursion limit, or holding an
    integer past the digit limit, is skipped like any unparseable line: the
    server keeps serving and answers the next request."""
    from tests.fixtures.unreadable_json import KINDS, handled, unreadable_object

    messages = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
        *(unreadable_object(kind, {"jsonrpc": "2.0", "id": 9, "method": "ping"}) for kind in KINDS),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    ]
    monkeypatch.setattr(context_mcp.sys, "stdin", io.StringIO("\n".join(messages) + "\n"))
    with handled("context_mcp.main"):
        context_mcp.main()
    out = capsys.readouterr()
    replies = [json.loads(line) for line in out.out.strip().splitlines()]
    assert [r["id"] for r in replies] == [1, 2]
    assert out.err.count("unparseable") == len(KINDS)
