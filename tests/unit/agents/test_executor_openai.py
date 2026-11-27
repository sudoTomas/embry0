"""OpenAICompatibleExecutor against a fake OpenAI-compatible HTTP server (real sockets)."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

import pytest

import embry0.agents.executor_openai as eo
from embry0.agents.executor_openai import OpenAICompatibleExecutor
from embry0.agents.invocation import AgentInvocation
from embry0.safety.policy import default_policy_for_agent
from tests.fixtures.openai_compatible_server import FakeOpenAIServer, completion, tool_call

_KEY = "sk-test-key-not-real-0123456789"


@pytest.fixture
def server():
    fake = FakeOpenAIServer(keys=[_KEY])
    with fake.running():
        yield fake


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(eo, "_RETRY_BASE_SECONDS", 0.0)
    return tmp_path


@pytest.fixture
def custom(monkeypatch, server, workspace):
    """The custom provider pointed at the fake server, as sandbox create injects it."""
    monkeypatch.setenv("EMBRY0_LLM_CUSTOM_BASE_URL", server.url)
    monkeypatch.setenv("EMBRY0_LLM_CUSTOM_API_KEY", _KEY)
    return server


@pytest.fixture
def catalog_endpoint(monkeypatch, server, workspace):
    """A catalog provider's calls sent to the fake server; records the root it would have called."""
    called: list[str] = []
    real = eo._make_client

    def make(base_url: str, api_key: str):
        called.append(base_url)
        return real(server.url, api_key)

    monkeypatch.setattr(eo, "_make_client", make)
    return called


def _invocation(provider: str = "custom", model: str = "custom/llama3.3:70b", **kw: Any) -> AgentInvocation:
    base: dict[str, Any] = {
        "agent_type": "developer",
        "prompt": "do the task",
        "system_prompt": "",
        "system_context": "",
        "model": model,
        "tools": ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        "skills": [],
        "mcp_servers": {},
        "max_turns": 10,
        "timeout_seconds": 30,
        "execution_mode": "sdk",
        "auth_mode": "oauth",
        "safety_policy": default_policy_for_agent("developer"),
        "channel_config": None,
        "provider": provider,
    }
    if provider == "custom":
        # The rates the orchestrator sends a run on a custom model the catalogue lists free: the provider prices
        # none of its own (#173).
        base["pricing_usd_per_mtok"] = (0.0, 0.0)
    base.update(kw)
    return AgentInvocation(**base)


def _run(inv: AgentInvocation, events: list[dict[str, Any]] | None = None):
    sink = events if events is not None else []
    return asyncio.run(OpenAICompatibleExecutor().run(inv, {"_test_writer": sink.append}))


def test_tool_call_round_trip(custom, workspace) -> None:
    (workspace / "notes.txt").write_text("the answer is 42\n")
    custom.answer(completion(tool_calls=[tool_call("call_1", "Read", {"file_path": "notes.txt"})], prompt_tokens=100))
    custom.answer(completion(content="the file says 42", prompt_tokens=150, completion_tokens=7))
    events: list[dict[str, Any]] = []

    out = _run(_invocation(), events)

    assert not out.is_error, out.error_message
    assert out.output == "the file says 42"
    assert out.tools_called == {"Read": 1}
    assert (out.input_tokens, out.output_tokens) == (250, 12), "token totals sum over both turns"
    assert out.cost_usd == 0.0, "a self-hosted model is recorded at $0"
    first, second = custom.chat_requests()
    assert first["path"] == "/v1/chat/completions"
    assert first["headers"]["Authorization"] == f"Bearer {_KEY}"
    assert first["json"]["model"] == "llama3.3:70b", "the routing prefix must not reach the server"
    assert first["json"]["messages"][0]["role"] == "system"
    assert first["json"]["messages"][-1] == {"role": "user", "content": "do the task"}
    read_def = next(t for t in first["json"]["tools"] if t["function"]["name"] == "Read")
    assert read_def["type"] == "function" and read_def["function"]["parameters"]["required"] == ["file_path"]
    assistant, tool_message = second["json"]["messages"][-2:]
    assert assistant["role"] == "assistant" and assistant["tool_calls"][0]["id"] == "call_1"
    assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "call_1"
    assert "the answer is 42" in tool_message["content"], "the tool really ran in the workspace"
    kinds = [e["type"] for e in events]
    assert kinds[0] == "agent_started" and kinds[-1] == "agent_completed"
    assert "tool_call" in kinds and "tool_result" in kinds and "cost_update" in kinds


def test_a_catalog_provider_calls_its_own_root_and_is_priced(catalog_endpoint, server, monkeypatch) -> None:
    monkeypatch.setenv("EMBRY0_LLM_DEEPSEEK_API_KEY", _KEY)
    server.answer(completion(content="done", prompt_tokens=1000, cached_tokens=200, completion_tokens=50))

    out = _run(_invocation(provider="deepseek", model="deepseek-flash"))

    assert not out.is_error, out.error_message
    assert catalog_endpoint == ["https://api.deepseek.com"]
    assert server.chat_requests()[0]["json"]["model"] == "deepseek-flash"
    assert (out.input_tokens, out.cache_read_tokens) == (800, 200), "cached prompt tokens are priced apart"
    expected = (800 * 0.30 + 200 * 0.30 * 0.1 + 50 * 1.20) / 1_000_000
    assert out.cost_usd == pytest.approx(expected)


def test_a_model_that_needs_request_fields_gets_them(catalog_endpoint, server, monkeypatch) -> None:
    monkeypatch.setenv("EMBRY0_LLM_OPENAI_API_KEY", _KEY)
    server.answer(completion(content="ok"))
    _run(_invocation(provider="openai", model="gpt-6-luna"))
    body = server.chat_requests()[0]["json"]
    assert body["reasoning_effort"] == "none", "gpt-6-luna calls tools on chat completions only with this"
    assert catalog_endpoint == ["https://api.openai.com/v1"]


#: The fields the executor sets or reads its answer by, written out here, not read from the code.
_EXECUTOR_FIELDS = [
    "model",
    "messages",
    "tools",
    "tool_choice",
    "functions",
    "function_call",
    "stream",
    "stream_options",
    "n",
]


def test_a_model_s_extra_fields_never_replace_the_executor_s_own(catalog_endpoint, server, monkeypatch) -> None:
    """The catalogue refuses request_extras outside its allowlist; whatever reaches the wire anyway, the request still
    carries the routed model, the run's messages and the executor's tools, and none of the executor's other fields."""
    monkeypatch.setenv("EMBRY0_LLM_OPENAI_API_KEY", _KEY)
    routed = eo.provider_named("openai")
    assert routed is not None
    extras: dict[str, Any] = {field: "from-the-extras" for field in _EXECUTOR_FIELDS}
    extras["reasoning_effort"] = "none"
    with_extras = dataclasses.replace(routed, request_extras={"gpt-6-luna": extras})
    monkeypatch.setattr(eo, "provider_named", lambda name: with_extras if name == "openai" else None)
    server.answer(completion(content="ok"))
    _run(_invocation(provider="openai", model="gpt-6-luna"))
    body = server.chat_requests()[0]["json"]
    assert body["model"] == "gpt-6-luna", f"a model's extra fields replaced the routed model: {body['model']!r}"
    assert [m["role"] for m in body["messages"]][:2] == ["system", "user"], (
        "a model's extra fields replaced the messages"
    )
    assert isinstance(body["tools"], list) and body["tools"], "a model's extra fields replaced the executor's tools"
    leaked = sorted(field for field, value in body.items() if value == "from-the-extras")
    assert leaked == [], f"a model's extra fields reached the request: {leaked}"
    assert body["reasoning_effort"] == "none", "the model's own extra field was dropped"


def test_the_executor_s_fields_win_over_an_extra_field_the_filter_misses(catalog_endpoint, server, monkeypatch) -> None:
    """The second guard alone: with no field filtered, the routed model and the run's messages still win."""
    monkeypatch.setenv("EMBRY0_LLM_OPENAI_API_KEY", _KEY)
    monkeypatch.setattr(eo, "EXECUTOR_FIELDS", frozenset())
    routed = eo.provider_named("openai")
    assert routed is not None
    with_extras = dataclasses.replace(routed, request_extras={"gpt-6-luna": {"model": "gpt-6.1-sol", "messages": []}})
    monkeypatch.setattr(eo, "provider_named", lambda name: with_extras if name == "openai" else None)
    server.answer(completion(content="ok"))
    _run(_invocation(provider="openai", model="gpt-6-luna"))
    body = server.chat_requests()[0]["json"]
    assert body["model"] == "gpt-6-luna", "an extra field the filter missed replaced the routed model"
    assert body["messages"], "an extra field the filter missed replaced the messages"


def test_a_denied_tool_is_answered_as_an_error_and_never_run(custom, workspace) -> None:
    custom.answer(completion(tool_calls=[tool_call("c1", "Bash", {"command": "rm -rf /"})]))
    custom.answer(completion(content="stopped"))

    out = _run(_invocation())

    assert out.policy_denials, "the dangerous command must be denied by the policy"
    tool_message = custom.chat_requests()[1]["json"]["messages"][-1]
    assert tool_message["role"] == "tool" and tool_message["content"].startswith("Error: ")


def test_malformed_tool_arguments_are_answered_not_executed(custom, workspace, monkeypatch) -> None:
    ran: list[str] = []
    monkeypatch.setattr(eo, "execute_tool", lambda name, tool_input, cwd, policy: ran.append(name) or ("x", False))
    custom.answer(completion(tool_calls=[tool_call("c1", "Read", '{"file_path": ')]))
    custom.answer(completion(content="ok"))

    out = _run(_invocation())

    assert not out.is_error
    assert ran == [], "a call whose arguments do not parse must not run"
    tool_message = custom.chat_requests()[1]["json"]["messages"][-1]
    assert "not valid JSON" in tool_message["content"]


def test_a_missing_key_fails_before_any_request(server, workspace, monkeypatch) -> None:
    monkeypatch.setenv("EMBRY0_LLM_CUSTOM_BASE_URL", server.url)
    monkeypatch.delenv("EMBRY0_LLM_CUSTOM_API_KEY", raising=False)
    out = _run(_invocation())
    assert out.is_error and "EMBRY0_LLM_CUSTOM_API_KEY" in out.error_message
    assert server.requests == []


def test_a_missing_custom_base_url_fails_before_any_request(server, workspace, monkeypatch) -> None:
    monkeypatch.delenv("EMBRY0_LLM_CUSTOM_BASE_URL", raising=False)
    monkeypatch.setenv("EMBRY0_LLM_CUSTOM_API_KEY", _KEY)
    out = _run(_invocation())
    assert out.is_error and "EMBRY0_LLM_CUSTOM_BASE_URL" in out.error_message
    assert server.requests == []


def test_a_rejected_key_is_auth_rejected_without_echoing_the_key(custom) -> None:
    custom.answer({"error": {"message": "Incorrect API key provided"}}, status=401)
    out = _run(_invocation())
    assert out.is_error and out.error_code == "ERR_AUTH_REJECTED"
    assert "Incorrect API key provided" in out.error_message
    assert _KEY not in out.error_message
    assert len(custom.chat_requests()) == 1, "a 401 is not retried"


def test_another_http_failure_is_an_upstream_error(custom) -> None:
    custom.answer({"error": {"message": "model llama9 not found"}}, status=404)
    out = _run(_invocation())
    assert out.error_code == "ERR_UPSTREAM_HTTP" and "model llama9 not found" in out.error_message
    assert len(custom.chat_requests()) == 1, "a 404 is not retried"


def test_a_server_error_is_retried(custom) -> None:
    custom.answer({"error": {"message": "overloaded"}}, status=503)
    custom.answer(completion(content="recovered"))
    out = _run(_invocation())
    assert not out.is_error and out.output == "recovered"
    assert len(custom.chat_requests()) == 2


def test_rate_limiting_that_persists_is_the_rate_limit_code(custom) -> None:
    for _ in range(3):
        custom.answer({"error": {"message": "slow down"}}, status=429)
    out = _run(_invocation())
    assert out.error_code == "ERR_PROVIDER_RATE_LIMITED"
    assert len(custom.chat_requests()) == 3, "two retries, then the error"


def test_the_turn_cap_is_a_max_turns_error(custom, workspace) -> None:
    (workspace / "a.txt").write_text("a")
    for i in range(2):
        custom.answer(completion(tool_calls=[tool_call(f"c{i}", "Read", {"file_path": "a.txt"})]))
    out = _run(_invocation(max_turns=2))
    assert out.is_error and out.error_code == "ERR_AGENT_MAX_TURNS"
    assert out.messages is not None, "a capped run keeps its conversation for the resume"


def test_reasoning_is_streamed_as_thinking_and_sent_back(custom) -> None:
    custom.answer(
        completion(
            tool_calls=[tool_call("c1", "Glob", {"pattern": "*.txt"})],
            reasoning_content="look for text files first",
        )
    )
    custom.answer(completion(content="none found"))
    events: list[dict[str, Any]] = []
    _run(_invocation(), events)
    assert any(e["type"] == "thinking" and "text files" in e["text"] for e in events)
    assistant = custom.chat_requests()[1]["json"]["messages"][-2]
    assert assistant["reasoning_content"] == "look for text files first"


def test_the_key_is_scrubbed_from_what_the_run_emits(custom) -> None:
    custom.answer(completion(content=f"my key is {_KEY}"))
    events: list[dict[str, Any]] = []
    _run(_invocation(), events)
    emitted = " ".join(str(e) for e in events)
    assert _KEY not in emitted, "the sandbox key leaked into the event stream"


def test_mcp_tool_results_go_back_as_text(custom, monkeypatch) -> None:
    class _FakeMcp:
        async def start(self) -> None:
            pass

        async def list_tools(self) -> list[dict[str, Any]]:
            return [{"name": "browser_navigate", "description": "go", "inputSchema": {"type": "object"}}]

        async def call_tool(self, tool: str, arguments: dict[str, Any], **_kw: Any) -> tuple[str, bool]:
            return (f"navigated to {arguments['url']}", False)

        async def close(self) -> None:
            pass

    monkeypatch.setattr(eo, "_make_mcp_client", lambda server_name, server_cfg: _FakeMcp())
    tools = ["Read", "mcp__playwright__browser_navigate"]
    policy = dataclasses.replace(default_policy_for_agent("qa"), allowed_tools=tools)
    inv = _invocation(
        agent_type="qa",
        tools=tools,
        safety_policy=policy,
        mcp_servers={"playwright": {"type": "stdio", "command": "playwright-mcp"}},
    )
    custom.answer(completion(tool_calls=[tool_call("c1", "mcp__playwright__browser_navigate", {"url": "http://app"})]))
    custom.answer(completion(content="done"))

    out = _run(inv)

    assert not out.is_error, out.error_message
    names = [t["function"]["name"] for t in custom.chat_requests()[0]["json"]["tools"]]
    assert "mcp__playwright__browser_navigate" in names
    assert custom.chat_requests()[1]["json"]["messages"][-1]["content"] == "navigated to http://app"


def test_select_executor_routes_the_family_to_this_executor() -> None:
    from embry0.agents.executor import SdkAgentExecutor
    from embry0.agents.executor_factory import select_executor

    for name, model in [("openai", "gpt-6-luna"), ("groq", "groq/openai/gpt-oss-20b"), ("custom", "custom/x")]:
        assert isinstance(select_executor(_invocation(provider=name, model=model)), OpenAICompatibleExecutor)
    assert isinstance(select_executor(_invocation(provider=None, model="claude-sonnet-5")), SdkAgentExecutor)


# ---------------------------------------------------------------------------
# The output limit every call names (#759): a metering proxy holds a call naming none to 128,000 output tokens.


def _body_of_first_call(server: Any) -> dict[str, Any]:
    return server.chat_requests()[0]["json"]


@pytest.mark.parametrize(
    ("provider", "model", "field_name"),
    [("deepseek", "deepseek-flash", "max_tokens"), ("openai", "gpt-6-luna", "max_completion_tokens")],
)
def test_every_call_names_an_output_limit(catalog_endpoint, server, monkeypatch, provider, model, field_name) -> None:
    """32,000 unless the catalogue says otherwise; OpenAI's reasoning models read it as max_completion_tokens."""
    monkeypatch.setenv(f"EMBRY0_LLM_{provider.upper()}_API_KEY", _KEY)
    server.answer(completion(content="ok"))
    _run(_invocation(provider=provider, model=model))
    body = _body_of_first_call(server)
    assert body.get(field_name) == 32_000, f"{model} was called with no output limit: {sorted(body)}"


def test_a_model_s_catalogue_entry_names_its_own_output_limit(catalog_endpoint, server, monkeypatch) -> None:
    monkeypatch.setenv("EMBRY0_LLM_DEEPSEEK_API_KEY", _KEY)
    routed = eo.provider_named("deepseek")
    assert routed is not None
    try:
        limited = dataclasses.replace(routed, output_limits={"deepseek-flash": 8_192})
    except TypeError as exc:
        pytest.fail(f"a model's entry cannot name an output limit: {exc}")
    monkeypatch.setattr(eo, "provider_named", lambda name: limited if name == "deepseek" else routed)
    server.answer(completion(content="ok"))
    _run(_invocation(provider="deepseek", model="deepseek-flash"))
    assert _body_of_first_call(server).get("max_tokens") == 8_192


def test_a_limit_the_model_s_request_extras_name_is_the_one_sent(catalog_endpoint, server, monkeypatch) -> None:
    monkeypatch.setenv("EMBRY0_LLM_DEEPSEEK_API_KEY", _KEY)
    routed = eo.provider_named("deepseek")
    assert routed is not None
    with_extras = dataclasses.replace(routed, request_extras={"deepseek-flash": {"max_tokens": 4_000}})
    monkeypatch.setattr(eo, "provider_named", lambda name: with_extras if name == "deepseek" else routed)
    server.answer(completion(content="ok"))
    _run(_invocation(provider="deepseek", model="deepseek-flash"))
    body = _body_of_first_call(server)
    assert (body.get("max_tokens"), "max_completion_tokens" in body) == (4_000, False), body


def _custom_record(**kw: Any) -> Any:
    """The record the orchestrator hands a run on a custom model (its server's address from the sandbox env)."""
    from embry0.agents.invocation import ProviderRecord

    base: dict[str, Any] = {
        "name": "custom",
        "runtime": "chat_completions",
        "upstream_model": "llama3.3:70b",
        "rates": (0.0, 0.0),
        "cache_read_factor": 0.0,
        "cache_write_factor": 0.0,
        "key_env": "EMBRY0_LLM_CUSTOM_API_KEY",
        "base_url_env": "EMBRY0_LLM_CUSTOM_BASE_URL",
    }
    try:
        return ProviderRecord(**{**base, **kw})
    except TypeError as exc:
        pytest.fail(f"a record cannot say its run is held to a budget: {exc}")


def _window_of(monkeypatch: pytest.MonkeyPatch, window: int | None) -> None:
    async def _window(*_a: Any, **_k: Any) -> int | None:
        return window

    monkeypatch.setattr(eo, "_context_window", _window)


def _room_by_estimate(body: dict[str, Any], window: int, *, calibrated: bool, ratio: float) -> int:
    """The room a request leaves by the executor's own estimate, less its margin (the estimate before the server has
    counted a request, an eighth of it after)."""
    from embry0.agents.context_fit import answer_reserve, request_chars

    estimate = int(request_chars(body["messages"][0]["content"], body.get("tools", []), body["messages"][1:]) * ratio)
    margin = estimate // 8 if calibrated else estimate
    return max(window - estimate - margin, answer_reserve(window))


@pytest.mark.parametrize(
    ("estimate", "calibrated", "room"),
    [
        (2_000, False, 28_768),
        (8_000, True, 23_768),
        (14_336, False, 4_096),
        (14_337, False, None),
        (20_000, False, None),
        (25_000, True, 4_643),
        (26_000, True, None),
    ],
    ids=[
        "near-empty",
        "calibrated",
        "just-the-reserve",
        "just-under-the-reserve",
        "estimate-doubled-past",
        "calibrated-near-full",
        "calibrated-past",
    ],
)
def test_the_room_a_request_leaves_is_less_a_margin_or_no_limit_below_the_fit_s_reserve(
    estimate: int, calibrated: bool, room: int | None
) -> None:
    """On a 32k window: the margin is the estimate until the server has counted a request, an eighth after. Below the
    fit's reserve (4,096) the request may be nearer the window's end than estimated, and names no limit: a limit past
    the room it truly leaves is refused where naming none is not."""
    try:
        from embry0.agents.context_fit import answer_room
    except ImportError as exc:
        pytest.fail(f"nothing works out the room a request leaves: {exc}")
    named = answer_room(32_768, estimate, calibrated=calibrated)
    assert named == room, f"a request of {estimate:,} tokens by estimate (calibrated: {calibrated}) asks for {named}"


def test_a_custom_model_in_a_run_with_no_budget_names_no_output_limit(custom, monkeypatch) -> None:
    """Its server's default stands (vLLM's is the room the request leaves): a limit of the fit's reserve cut every
    answer at 2,048 tokens on a 16k window, a near-empty conversation's too."""
    _window_of(monkeypatch, 16_384)
    custom.answer(completion(content="ok"))
    _run(_invocation(provider_record=_custom_record()))
    body = _body_of_first_call(custom)
    assert "max_tokens" not in body and "max_completion_tokens" not in body, f"a run with no budget named {body}"


def test_a_custom_model_in_a_run_with_a_budget_names_the_room_its_request_leaves(
    custom, workspace, monkeypatch
) -> None:
    """A metering proxy holds a call naming no limit to 128,000 output tokens, so a budgeted run names one: the room
    the request leaves less a margin for the estimate, on every turn, not the fit's reserve."""
    from embry0.agents.context_fit import answer_reserve

    window = 32_768
    _window_of(monkeypatch, window)
    (workspace / "notes.txt").write_text("the answer is 42\n")
    custom.answer(completion(tool_calls=[tool_call("call_1", "Read", {"file_path": "notes.txt"})], prompt_tokens=0))
    custom.answer(completion(tool_calls=[tool_call("call_2", "Read", {"file_path": "notes.txt"})], prompt_tokens=9_000))
    custom.answer(completion(content="done", prompt_tokens=9_500))
    out = _run(_invocation(provider_record=_custom_record(budgeted=True)))
    assert not out.is_error, out.error_message
    first, second, third = (r["json"] for r in custom.chat_requests())
    named = [first.get("max_tokens"), second.get("max_tokens"), third.get("max_tokens")]
    assert named[0] == _room_by_estimate(first, window, calibrated=False, ratio=0.3), f"each call named {named}"
    assert named[0] > answer_reserve(window), f"a near-empty conversation was given the fit's reserve: {named}"
    # A server that counted no input leaves the estimate where it was, and calibrated no more than before.
    assert named[1] == _room_by_estimate(second, window, calibrated=False, ratio=0.3), f"each call named {named}"
    from embry0.agents.context_fit import request_chars

    ratio = 9_000 / request_chars(second["messages"][0]["content"], second.get("tools", []), second["messages"][1:])
    assert named[2] == _room_by_estimate(third, window, calibrated=True, ratio=ratio), f"each call named {named}"


def test_a_request_near_its_window_s_end_by_estimate_names_no_limit(custom, monkeypatch) -> None:
    """32k window, a first request of about 21,000 tokens by estimate: what it truly holds may be more, so a limit of the
    fit's reserve could pass the room it leaves and be refused where naming none is not."""
    _window_of(monkeypatch, 32_768)
    custom.answer(completion(content="ok"))
    out = _run(_invocation(prompt="word " * 14_000, provider_record=_custom_record(budgeted=True)))
    assert not out.is_error, out.error_message
    body = _body_of_first_call(custom)
    assert "max_tokens" not in body and "max_completion_tokens" not in body, f"named {body.get('max_tokens')}"


_TOO_LONG = {
    "error": {
        "message": "This model's maximum context length is 32768 tokens. However, you requested 33010 tokens "
        "(28914 in the messages, 4096 in the completion). Please reduce the length of the messages or completion.",
        "type": "BadRequestError",
    }
}


def test_a_limit_refused_as_past_the_window_goes_again_naming_none(custom, monkeypatch) -> None:
    """The server counted more than estimated: what it refused was the limit, and naming none it answers in the room
    the request truly leaves. Naming a limit never fails a request that naming none would not."""
    _window_of(monkeypatch, 32_768)
    custom.answer(_TOO_LONG, status=400)
    custom.answer(completion(content="done"))
    out = _run(_invocation(provider_record=_custom_record(budgeted=True)))
    assert not out.is_error, out.error_message
    first, second = (r["json"] for r in custom.chat_requests())
    assert "max_tokens" in first, f"the first call named no limit: {sorted(first)}"
    assert "max_tokens" not in second, f"the call went again naming {second.get('max_tokens')}"
    assert {k: v for k, v in first.items() if k != "max_tokens"} == second, "the call went again changed"


@pytest.mark.parametrize(
    ("answer", "window"),
    [
        pytest.param({"error": {"message": "tools[0] is not a valid tool"}}, 32_768, id="another-refusal"),
        pytest.param(_TOO_LONG, None, id="no-known-window"),
    ],
)
def test_only_a_refusal_as_past_a_known_window_sends_the_call_again(custom, monkeypatch, answer, window) -> None:
    _window_of(monkeypatch, window)
    custom.answer(answer, status=400)
    out = _run(_invocation(provider_record=_custom_record(budgeted=True)))
    assert out.is_error, "the run took a refusal as an answer"
    assert len(custom.chat_requests()) == 1, f"the call went {len(custom.chat_requests())} times"


def test_a_custom_model_in_a_run_with_a_budget_names_the_default_when_its_window_is_unknown(
    custom, monkeypatch
) -> None:
    _window_of(monkeypatch, None)
    custom.answer(completion(content="ok"))
    _run(_invocation(provider_record=_custom_record(budgeted=True)))
    assert _body_of_first_call(custom).get("max_tokens") == 32_000


def test_a_custom_model_s_own_limit_is_named_within_the_room_its_request_leaves(custom, monkeypatch) -> None:
    """An entry's max_output_tokens is the operator's, budget or not; a known window still bounds it."""
    _window_of(monkeypatch, 8_192)
    custom.answer(completion(content="ok"))
    _run(_invocation(provider_record=_custom_record(max_output_tokens=100_000)))
    body = _body_of_first_call(custom)
    assert body.get("max_tokens") == _room_by_estimate(body, 8_192, calibrated=False, ratio=0.3), body


def test_a_custom_model_of_unknown_window_naming_no_limit_keeps_its_server_s_default(custom, monkeypatch) -> None:
    _window_of(monkeypatch, None)
    custom.answer(completion(content="ok"))
    _run(_invocation())
    body = _body_of_first_call(custom)
    assert "max_tokens" not in body and "max_completion_tokens" not in body, body


# ---------------------------------------------------------------------------
# An answer cut at its output limit is not an end of turn (#759): the run fails, saying so.


def _cut(**kw: Any) -> dict[str, Any]:
    answer = completion(**kw)
    answer["choices"][0]["finish_reason"] = "length"
    return answer


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(lambda: _cut(content='the verdict is {"appro'), id="a text answer"),
        pytest.param(
            lambda: _cut(tool_calls=[tool_call("call_1", "Write", '{"file_path": "out.txt", "content": "par')]),
            id="a half-written tool call",
        ),
        pytest.param(
            lambda: _cut(tool_calls=[tool_call("call_1", "Write", {"file_path": "out.txt", "content": "x"})]),
            id="a whole tool call",
        ),
    ],
)
def test_an_answer_cut_at_its_output_limit_fails_the_run(custom, workspace, answer) -> None:
    custom.answer(answer())
    events: list[dict[str, Any]] = []
    out = _run(_invocation(), events)
    assert (out.is_error, out.error_code) == (True, "ERR_AGENT_ANSWER_CUT"), (out.is_error, out.error_code)
    assert "output limit" in (out.error_message or ""), out.error_message
    assert len(custom.chat_requests()) == 1, "the run went on past a cut answer"
    assert not (workspace / "out.txt").exists(), "a tool call of a cut answer was run"
    assert out.tools_called == {}, out.tools_called
    codes = [e.get("code") for e in events if e.get("type") == "error"]
    assert "ERR_AGENT_ANSWER_CUT" in codes, f"the run's error events: {codes}"


def test_the_run_s_record_carries_the_model_s_output_limit_to_the_sandbox(server, workspace, monkeypatch) -> None:
    from embry0.agents.invocation import ProviderRecord

    monkeypatch.setenv("EMBRY0_LLM_DEEPSEEK_API_KEY", _KEY)
    try:
        record = ProviderRecord(
            name="deepseek",
            runtime="chat_completions",
            upstream_model="deepseek-flash",
            rates=(0.3, 1.2),
            cache_read_factor=0.1,
            cache_write_factor=1.0,
            api_root=server.url,
            key_env="EMBRY0_LLM_DEEPSEEK_API_KEY",
            max_output_tokens=6_000,  # type: ignore[call-arg]
        )
    except TypeError as exc:
        pytest.fail(f"a record cannot carry a model's output limit: {exc}")
    server.answer(completion(content="ok"))
    _run(_invocation(provider="deepseek", model="deepseek-flash", provider_record=record))
    assert _body_of_first_call(server).get("max_tokens") == 6_000
