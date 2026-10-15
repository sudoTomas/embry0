"""Tests for DirectXaiExecutor (EMB-45) — the direct-xAI agentic loop.

Uses a fake Anthropic client injected via the module-level _make_client seam, so the
real SDK and network are never needed.
"""

from __future__ import annotations

import dataclasses

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


@pytest.mark.asyncio
async def test_xai_denied_tool_error_event_uses_message_code_and_ids(env, monkeypatch) -> None:
    _install(
        monkeypatch,
        [
            _Resp([_Block("tool_use", id="t1", name="Bash", input={"command": "rm -rf /"})], "tool_use", _Usage(5, 5)),
            _Resp([_Block("text", text="done")], "end_turn", _Usage(5, 5)),
        ],
    )
    events: list[dict] = []
    inv = dataclasses.replace(_invocation(), run_id="run-x", node="developer")
    await DirectXaiExecutor().run(inv, config={"configurable": {}, "_test_writer": events.append})
    err = next(e for e in events if e["type"] == "error")
    assert err["tool_name"] == "Bash"
    assert err["code"] == "ERR_TOOL_DENIED", f"denied tool error must carry the policy code: {err}"
    assert "error" not in err and err["message"], f"error event must carry message, not error: {err}"
    for e in events:
        assert e.get("run_id") == "run-x" and e.get("node") == "developer", f"{e['type']} lacks ids"


@pytest.mark.asyncio
async def test_xai_tool_result_uses_tool_id_and_marker(env, monkeypatch) -> None:
    _install(
        monkeypatch,
        [
            _Resp([_Block("tool_use", id="t1", name="Bash", input={"command": "echo hi"})], "tool_use", _Usage(5, 5)),
            _Resp([_Block("text", text="done")], "end_turn", _Usage(5, 5)),
        ],
    )
    monkeypatch.setattr(ex, "execute_tool", lambda name, tool_input, cwd, policy: ("y" * 4100, False))
    events: list[dict] = []
    await DirectXaiExecutor().run(_invocation(), config={"configurable": {}, "_test_writer": events.append})
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["tool_id"] == "t1", f"tool_result event must be keyed by tool_id: {result}"
    assert result["truncated"] is True and result["original_length"] == 4100


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


def _anthropic_exceptions(monkeypatch):
    """The unit env has no anthropic package. The executor imports it at the failure."""
    import sys
    import types

    mod = types.ModuleType("anthropic")

    class RateLimitError(Exception):
        pass

    class AuthenticationError(Exception):
        pass

    class PermissionDeniedError(Exception):
        pass

    mod.RateLimitError = RateLimitError
    mod.AuthenticationError = AuthenticationError
    mod.PermissionDeniedError = PermissionDeniedError
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return mod


def _raise_on_create(monkeypatch, exc: Exception) -> None:
    client = _FakeClient([])

    async def create(**_kw):
        raise exc

    client.messages.create = create
    monkeypatch.setattr(ex, "_make_client", lambda base_url, auth_token: client)


def test_xai_rate_limit_error_is_the_rate_limit_code(env, monkeypatch):
    mod = _anthropic_exceptions(monkeypatch)
    _raise_on_create(monkeypatch, mod.RateLimitError("nope"))
    out = _run(_invocation(), [])
    assert out.is_error is True
    assert out.error_code == "ERR_PROVIDER_RATE_LIMITED", "anthropic.RateLimitError must map to the rate-limit code"


def test_xai_authentication_error_is_auth_rejected(env, monkeypatch):
    mod = _anthropic_exceptions(monkeypatch)
    _raise_on_create(monkeypatch, mod.AuthenticationError("nope"))
    out = _run(_invocation(), [])
    assert out.error_code == "ERR_AUTH_REJECTED", "anthropic.AuthenticationError must be auth rejected"


def test_xai_permission_denied_is_auth_rejected(env, monkeypatch):
    mod = _anthropic_exceptions(monkeypatch)
    _raise_on_create(monkeypatch, mod.PermissionDeniedError("nope"))
    out = _run(_invocation(), [])
    assert out.error_code == "ERR_AUTH_REJECTED", "anthropic.PermissionDeniedError must be auth rejected"


@pytest.mark.parametrize(
    ("body", "code", "says"),
    [
        (
            "Error code: 403 - {'code': 'personal-team-blocked:spending-limit', 'error': 'Team spending limit reached'}",
            "ERR_PROVIDER_QUOTA_EXHAUSTED",
            "spending limit",
        ),
        (
            "Error code: 403 - {'error': 'The OAuth2 access token could not be validated [WKE=unauthenticated:x]'}",
            "ERR_PROVIDER_TOKEN_STALE",
            "stale",
        ),
        (
            "Error code: 403 - {'error': 'You do not have an active Grok subscription.'}",
            "ERR_AUTH_REJECTED",
            "no active SuperGrok subscription",
        ),
    ],
)
def test_xai_403_is_classified_by_its_body(env, monkeypatch, body, code, says):
    """A spent spending cap and a stale token are not refused credentials: read as auth
    rejected, each quarantined the grok account with no end."""
    mod = _anthropic_exceptions(monkeypatch)
    _raise_on_create(monkeypatch, mod.PermissionDeniedError(body))
    out = _run(_invocation(), [])
    assert out.is_error is True
    assert out.error_code == code, f"403 body {body!r} must be {code}, got {out.error_code!r}"
    assert says.lower() in out.error_message.lower(), f"the run's error must say {says!r}: {out.error_message!r}"
    assert body in out.error_message, "xAI's own words stay in the error"


def test_xai_other_exception_uses_the_text_classifier(env, monkeypatch):
    _raise_on_create(monkeypatch, RuntimeError("usage limit reached"))
    out = _run(_invocation(), [])
    assert out.is_error is True
    assert out.error_code == "ERR_PROVIDER_QUOTA_EXHAUSTED", "an untyped xAI failure must use the text classifier"


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


def test_cost_update_num_turns_counts_turns_not_distinct_tools(env, monkeypatch):
    # RAV-1241: three turns using the SAME tool twice must report 3 turns,
    # not len(tools_called) (which would be 1 — the number of distinct names).
    (env / "a.txt").write_text("a\n")
    responses = [
        _Resp([_Block("tool_use", id="t1", name="Read", input={"file_path": "a.txt"})], "tool_use", _Usage(10, 2)),
        _Resp([_Block("tool_use", id="t2", name="Read", input={"file_path": "a.txt"})], "tool_use", _Usage(10, 2)),
        _Resp([_Block("text", text="done")], "end_turn", _Usage(5, 2)),
    ]
    _install(monkeypatch, responses)
    events = []
    out = _run(_invocation(), events)
    assert not out.is_error
    assert out.tools_called == {"Read": 2}
    cost_updates = [e for e in events if e["type"] == "cost_update"]
    assert cost_updates and cost_updates[-1]["num_turns"] == 3


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


# ---- #329: final text is joined per message, tail cut on a line ------------


def _two_turns(first: str, second: str):
    return [
        _Resp(
            [_Block("text", text=first), _Block("tool_use", id="tu1", name="Glob", input={"pattern": "*.none"})],
            "tool_use",
        ),
        _Resp([_Block("text", text=second)], "end_turn"),
    ]


@pytest.mark.parametrize("token", ["TITLE: Fix the mailbox quota", "NO_ISSUE", '{"decision": "approve"}'])
def test_second_message_starts_on_its_own_line(env, monkeypatch, token):
    _install(monkeypatch, _two_turns("I checked the existing tickets.", token))
    out = _run(_invocation(), [])
    assert not out.is_error
    assert out.output == f"I checked the existing tickets.\n\n{token}"
    assert token in out.output.splitlines()


def test_blocks_of_one_message_are_not_split(env, monkeypatch):
    _install(
        monkeypatch,
        [_Resp([_Block("text", text="The rate is "), _Block("text", text="12 per minute.")], "end_turn")],
    )
    out = _run(_invocation(), [])
    assert out.output == "The rate is 12 per minute."


def test_tail_is_cut_after_joining_on_a_line_boundary(env, monkeypatch):
    _install(monkeypatch, _two_turns("narration line\n" * 2000, "NO_ISSUE"))
    out = _run(_invocation(), [])
    assert len(out.output) <= 10000
    assert out.output.startswith("narration line\n")
    assert out.output.splitlines()[-1] == "NO_ISSUE"


# ---- turn cap and resume (RAV-1260 contract) -----------------------------


def _tool_turn(i: int) -> _Resp:
    return _Resp(
        [_Block("text", text=f"step {i}"), _Block("tool_use", id=f"t{i}", name="Glob", input={"pattern": "*.py"})],
        "tool_use",
    )


def test_turn_cap_is_a_max_turns_error_the_pipeline_can_recover(env, monkeypatch):
    """A run still calling tools when it reaches max_turns died at the cap, it did not finish.

    The pipeline recovers a cap death — triage/review resume once to decide now, the developer
    commits its unfinished work — only for an output that is an error with a max-turns message
    and that kept its messages. Returned as a clean finish, every grok run that hit its cap was
    read as "no decision / no branch / no verdict" instead (4 of 8 developer runs, 2026-10-03/04).
    """
    from embry0.agents.session import is_max_turns_error
    from embry0.workflows.issue_to_pr.nodes._common import _max_turns_recovery_session

    _install(monkeypatch, [_tool_turn(1), _tool_turn(2)])
    out = _run(dataclasses.replace(_invocation(), max_turns=2), [])

    assert out.is_error is True, "a run cut off at max_turns must not look like a finished run"
    assert is_max_turns_error(out.error_message), f"not recognisable as a turn-cap death: {out.error_message!r}"
    assert out.error_code == "ERR_AGENT_MAX_TURNS", f"wrong error code for a turn-cap death: {out.error_code!r}"
    assert out.messages, "a turn-cap death must keep its conversation for the decide-now resume"
    assert "step 2" in out.output, "the narration up to the cap is still the run's output"
    entry = {"is_error": out.is_error, "error_message": out.error_message, "messages": out.messages}
    recovery = _max_turns_recovery_session({"agent_outputs": [entry]}, agent_type="review", job_id="j", credentials={})
    assert recovery is not None, "the pipeline's decide-now recovery must accept this output"


def test_resumed_transcript_is_replayed_with_alternating_roles(env, monkeypatch):
    """The text capture holds one assistant entry per turn, back to back.

    The decide-now resume of a turn-cap death replays exactly that capture. Consecutive
    same-role messages are not a well-formed Messages conversation; Anthropic merges them
    server side, xAI's compat layer does not document doing so, so they are merged here.
    """
    import asyncio

    client = _install(monkeypatch, [_Resp([_Block("text", text='{"decision": "approved"}')], "end_turn")])
    prior = [
        {"role": "user", "content": "review it"},
        {"role": "assistant", "content": "I'll read the diff."},
        {"role": "assistant", "content": "The diff is fine."},
    ]
    out = asyncio.run(
        DirectXaiExecutor().run(_invocation(prompt="decide now"), {"_test_writer": [].append}, resume_messages=prior)
    )

    assert not out.is_error
    sent = client.messages.calls[0]["messages"]
    roles = [m["role"] for m in sent]
    assert all(a != b for a, b in zip(roles, roles[1:], strict=False)), f"consecutive same-role messages sent: {roles}"
    assert roles == ["user", "assistant", "user"], roles
    assert "I'll read the diff." in sent[1]["content"] and "The diff is fine." in sent[1]["content"]
    assert sent[-1]["content"] == "decide now"


def test_grep_result_leaves_out_files_a_read_deny_rule_covers(env, monkeypatch):
    """#542: the executor hands its policy to Grep, so a cleared root cannot leak a denied file."""
    (env / "secret").mkdir()
    (env / "secret" / "key.txt").write_text("token=s3cr3t-value\n")
    (env / "open.txt").write_text("token=s3cr3t-open\n")
    inv = _invocation()
    policy = inv.safety_policy
    inv = dataclasses.replace(
        inv, safety_policy=dataclasses.replace(policy, deny_rules=[*policy.deny_rules, "Read(/secret/**)"])
    )
    responses = [
        _Resp([_Block("tool_use", id="t", name="Grep", input={"pattern": "s3cr3t"})], "tool_use", _Usage(10, 2)),
        _Resp([_Block("text", text="done")], "end_turn", _Usage(5, 2)),
    ]
    client = _install(monkeypatch, responses)

    out = _run(inv, [])

    assert not out.is_error
    tr = client.messages.calls[1]["messages"][-1]["content"][0]
    assert "s3cr3t-value" not in tr["content"], f"the executor's Grep returned a Read-denied file: {tr['content']!r}"
    assert "s3cr3t-open" in tr["content"], f"the allowed file must still be found: {tr['content']!r}"
