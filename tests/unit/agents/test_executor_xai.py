"""Tests for DirectXaiExecutor (EMB-45) — the direct-xAI agentic loop.

Uses a fake Anthropic client injected via the module-level _make_client seam, so the
real SDK and network are never needed.
"""

from __future__ import annotations

import pytest

import embry0.agents.executor_xai as ex
from embry0.agents.executor_xai import DirectXaiExecutor
from embry0.agents.invocation import AgentInvocation
from embry0.safety.policy import default_policy_for_agent

# ---- fake Anthropic client ----------------------------------------------


class _Block:
    def __init__(self, type_: str, **kw):
        self.type = type_
        for k, v in kw.items():
            setattr(self, k, v)


class _Usage:
    def __init__(self, i=0, o=0, cr=0, cc=0):
        self.input_tokens = i
        self.output_tokens = o
        self.cache_read_input_tokens = cr
        self.cache_creation_input_tokens = cc


class _Resp:
    def __init__(self, content, stop_reason, usage=None):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage or _Usage()


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def create(self, **kw):
        # Snapshot the messages list — the executor mutates it in place across turns,
        # so a live reference would show the final state, not the state at call time.
        kw = dict(kw)
        kw["messages"] = list(kw.get("messages", []))
        self.calls.append(kw)
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)
        self.closed = False

    async def close(self):
        self.closed = True


def _install(monkeypatch, responses):
    client = _FakeClient(responses)
    monkeypatch.setattr(ex, "_make_client", lambda base_url, auth_token: client)
    return client


def _invocation(prompt="do the task", tools=None):
    return AgentInvocation(
        agent_type="developer",
        prompt=prompt,
        system_prompt="",
        system_context="",
        model="grok-4.5",
        tools=tools or ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        skills=[],
        mcp_servers={},
        max_turns=10,
        timeout_seconds=30,
        execution_mode="sdk",
        auth_mode="oauth",
        safety_policy=default_policy_for_agent("developer"),
        channel_config=None,
        provider="xai",
    )


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBRY0_XAI_PROXY_URL", "http://xai-proxy:9106")
    monkeypatch.setenv("EMBRY0_XAI_PROXY_TOKEN", "sandbox-bearer-token")
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


def _run(inv, events):
    import asyncio

    return asyncio.run(DirectXaiExecutor().run(inv, {"_test_writer": events.append}))


# ---- tests ---------------------------------------------------------------


def test_missing_proxy_url_errors(monkeypatch, tmp_path):
    monkeypatch.delenv("EMBRY0_XAI_PROXY_URL", raising=False)
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    out = _run(_invocation(), [])
    assert out.is_error and "EMBRY0_XAI_PROXY_URL" in out.error_message


def test_missing_token_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBRY0_XAI_PROXY_URL", "http://xai-proxy:9106")
    monkeypatch.delenv("EMBRY0_XAI_PROXY_TOKEN", raising=False)
    monkeypatch.setenv("EMBRY0_XAI_PROXY_TOKEN_PATH", str(tmp_path / "nope"))
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    out = _run(_invocation(), [])
    assert out.is_error and "bearer" in out.error_message


def test_simple_text_turn(env, monkeypatch):
    _install(monkeypatch, [_Resp([_Block("text", text="all done")], "end_turn", _Usage(100, 20))])
    events = []
    out = _run(_invocation(), events)
    assert not out.is_error
    assert out.output == "all done"
    assert out.input_tokens == 100 and out.output_tokens == 20
    # grok-4.5 pricing (2.0, 6.0) $/Mtok
    assert out.cost_usd == pytest.approx(100 / 1e6 * 2.0 + 20 / 1e6 * 6.0)
    types = [e["type"] for e in events]
    assert types[0] == "agent_started"
    assert "turn_start" in types and "text" in types
    assert types[-1] == "agent_completed"
    assert out.messages == [{"role": "user", "content": "do the task"}, {"role": "assistant", "content": "all done"}]


def test_tool_use_executes_and_continues(env, monkeypatch):
    (env / "target.txt").write_text("hello world\n")
    responses = [
        _Resp(
            [_Block("tool_use", id="tu1", name="Read", input={"file_path": "target.txt"})],
            "tool_use",
            _Usage(50, 10),
        ),
        _Resp([_Block("text", text="the file says hello world")], "end_turn", _Usage(60, 15)),
    ]
    client = _install(monkeypatch, responses)
    events = []
    out = _run(_invocation(), events)
    assert not out.is_error
    assert "hello world" in out.output
    assert out.tools_called == {"Read": 1}
    # Token totals accumulate across both turns.
    assert out.input_tokens == 110 and out.output_tokens == 25
    # Second create() got a tool_result user message carrying the file content.
    second_msgs = client.messages.calls[1]["messages"]
    tool_result_msg = second_msgs[-1]
    assert tool_result_msg["role"] == "user"
    assert tool_result_msg["content"][0]["type"] == "tool_result"
    assert "hello world" in tool_result_msg["content"][0]["content"]
    # tool_call + tool_result events emitted.
    types = [e["type"] for e in events]
    assert "tool_call" in types and "tool_result" in types


def test_denied_tool_returns_error_result_not_executed(env, monkeypatch):
    responses = [
        _Resp(
            [_Block("tool_use", id="tu1", name="Read", input={"file_path": "/etc/passwd"})],
            "tool_use",
            _Usage(30, 5),
        ),
        _Resp([_Block("text", text="ok I will not read that")], "end_turn", _Usage(20, 5)),
    ]
    client = _install(monkeypatch, responses)
    events = []
    out = _run(_invocation(), events)
    assert not out.is_error
    # The tool_result fed back to the model is an error carrying the deny reason.
    tr = client.messages.calls[1]["messages"][-1]["content"][0]
    assert tr["is_error"] is True
    assert "deny rule" in tr["content"] or "Blocked by safety policy" in tr["content"]
    # An error event was emitted for the denial.
    assert any(e["type"] == "error" for e in events)


def test_dangerous_bash_denied(env, monkeypatch):
    responses = [
        _Resp([_Block("tool_use", id="t", name="Bash", input={"command": "rm -rf /"})], "tool_use", _Usage(10, 2)),
        _Resp([_Block("text", text="stopped")], "end_turn", _Usage(5, 2)),
    ]
    client = _install(monkeypatch, responses)
    out = _run(_invocation(), [])
    assert not out.is_error
    tr = client.messages.calls[1]["messages"][-1]["content"][0]
    assert tr["is_error"] is True


def test_tool_not_in_allowlist_denied(env, monkeypatch):
    # developer allowlist excludes browser tools; a stray tool name is name-gated.
    responses = [
        _Resp([_Block("tool_use", id="t", name="WebFetch", input={"url": "http://x"})], "tool_use", _Usage(10, 2)),
        _Resp([_Block("text", text="done")], "end_turn", _Usage(5, 2)),
    ]
    client = _install(monkeypatch, responses)
    _run(_invocation(), [])
    tr = client.messages.calls[1]["messages"][-1]["content"][0]
    assert tr["is_error"] is True
    assert "allowlist" in tr["content"]


def test_client_closed(env, monkeypatch):
    client = _install(monkeypatch, [_Resp([_Block("text", text="x")], "end_turn")])
    _run(_invocation(), [])
    assert client.closed is True


# ---- MCP (Phase C) --------------------------------------------------------


class _FakeMcp:
    """Stands in for McpStdioClient — no subprocess, records calls."""

    def __init__(self):
        self.started = False
        self.closed = False
        self.calls = []

    async def start(self):
        self.started = True

    async def list_tools(self):
        return [
            {
                "name": "browser_navigate",
                "description": "Navigate to a URL",
                "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            },
            {"name": "browser_run_code_unsafe", "description": "run js", "inputSchema": {"type": "object"}},
        ]

    async def call_tool(self, tool, arguments, **_kw):
        self.calls.append((tool, arguments))
        return (f"Navigated to {arguments.get('url', '')}", False)

    async def close(self):
        self.closed = True


_QA_TOOLS = ["Read", "Bash", "mcp__playwright__browser_navigate"]


def _qa_invocation(tools=None):
    import dataclasses

    tools = tools or list(_QA_TOOLS)
    # Mirror sandbox/runner.py: policy.allowed_tools is overwritten with the
    # invocation's tool list, which for qa includes the mcp__playwright__* names.
    policy = dataclasses.replace(default_policy_for_agent("qa"), allowed_tools=list(tools))
    return AgentInvocation(
        agent_type="qa",
        prompt="validate the app",
        system_prompt="",
        system_context="",
        model="grok-4.5",
        tools=tools,
        skills=[],
        mcp_servers={"playwright": {"type": "stdio", "command": "playwright-mcp", "args": ["--headless"]}},
        max_turns=10,
        timeout_seconds=30,
        execution_mode="sdk",
        auth_mode="oauth",
        safety_policy=policy,
        channel_config=None,
        provider="xai",
    )


def _install_mcp(monkeypatch):
    fake = _FakeMcp()
    monkeypatch.setattr(ex, "_make_mcp_client", lambda server_name, server_cfg: fake)
    return fake


def test_mcp_tools_exposed_filtered_to_allowlist(env, monkeypatch):
    client = _install(monkeypatch, [_Resp([_Block("text", text="done")], "end_turn")])
    mcp = _install_mcp(monkeypatch)
    out = _run(_qa_invocation(), [])
    assert not out.is_error
    assert mcp.started is True
    tool_names = [d["name"] for d in client.messages.calls[0]["tools"]]
    assert tool_names == ["Read", "Bash", "mcp__playwright__browser_navigate"]
    # The server offers browser_run_code_unsafe but the allowlist doesn't → never exposed (EMB-37).
    assert "mcp__playwright__browser_run_code_unsafe" not in tool_names


def test_mcp_tool_routed_to_client_and_result_fed_back(env, monkeypatch):
    responses = [
        _Resp(
            [
                _Block(
                    "tool_use",
                    id="tu1",
                    name="mcp__playwright__browser_navigate",
                    input={"url": "http://app:3000/login"},
                )
            ],
            "tool_use",
            _Usage(50, 10),
        ),
        _Resp([_Block("text", text="page loaded")], "end_turn", _Usage(60, 15)),
    ]
    client = _install(monkeypatch, responses)
    mcp = _install_mcp(monkeypatch)
    events = []
    out = _run(_qa_invocation(), events)
    assert not out.is_error
    assert mcp.calls == [("browser_navigate", {"url": "http://app:3000/login"})]
    assert out.tools_called == {"mcp__playwright__browser_navigate": 1}
    tr = client.messages.calls[1]["messages"][-1]["content"][0]
    assert tr["type"] == "tool_result"
    assert tr["is_error"] is False
    assert "Navigated to http://app:3000/login" in tr["content"]
    types = [e["type"] for e in events]
    assert "tool_call" in types and "tool_result" in types


def test_mcp_tool_not_in_allowlist_denied_before_dispatch(env, monkeypatch):
    responses = [
        _Resp(
            [_Block("tool_use", id="t", name="mcp__playwright__browser_run_code_unsafe", input={"code": "1+1"})],
            "tool_use",
            _Usage(10, 2),
        ),
        _Resp([_Block("text", text="ok")], "end_turn", _Usage(5, 2)),
    ]
    client = _install(monkeypatch, responses)
    mcp = _install_mcp(monkeypatch)
    _run(_qa_invocation(), [])
    tr = client.messages.calls[1]["messages"][-1]["content"][0]
    assert tr["is_error"] is True
    assert "allowlist" in tr["content"]
    assert mcp.calls == []  # gated before dispatch — the server never saw it


def test_builtins_still_work_alongside_mcp(env, monkeypatch):
    (env / "notes.txt").write_text("qa notes\n")
    responses = [
        _Resp([_Block("tool_use", id="t1", name="Read", input={"file_path": "notes.txt"})], "tool_use", _Usage(10, 2)),
        _Resp([_Block("text", text="read it")], "end_turn", _Usage(5, 2)),
    ]
    client = _install(monkeypatch, responses)
    mcp = _install_mcp(monkeypatch)
    out = _run(_qa_invocation(), [])
    assert not out.is_error
    assert mcp.calls == []
    tr = client.messages.calls[1]["messages"][-1]["content"][0]
    assert "qa notes" in tr["content"]


def test_mcp_clients_closed_after_run(env, monkeypatch):
    _install(monkeypatch, [_Resp([_Block("text", text="x")], "end_turn")])
    mcp = _install_mcp(monkeypatch)
    _run(_qa_invocation(), [])
    assert mcp.closed is True


def test_mcp_startup_failure_is_job_error(env, monkeypatch):
    _install(monkeypatch, [_Resp([_Block("text", text="never reached")], "end_turn")])
    fake = _install_mcp(monkeypatch)

    async def broken_start():
        raise RuntimeError("playwright-mcp: command not found")

    fake.start = broken_start
    events = []
    out = _run(_qa_invocation(), events)
    assert out.is_error
    assert "MCP server startup failed" in out.error_message
    assert fake.closed is True  # partially-started clients are torn down
    assert any(e["type"] == "error" for e in events)
