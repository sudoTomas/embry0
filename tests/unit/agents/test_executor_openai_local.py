"""The chat-completions executor on the local-llm-proxy: the bearer file, and the model's context window."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import embry0.agents.executor_openai as eo
from embry0.agents.context_fit import (
    ContextWindowExceeded,
    answer_reserve,
    calibrated_ratio,
    fit_messages,
    window_from_models,
)
from embry0.agents.executor_openai import OpenAICompatibleExecutor
from embry0.safety.error_codes import ErrorCode, classify_agent_error
from tests.fixtures.openai_compatible_server import FakeOpenAIServer, completion, tool_call
from tests.unit.agents.test_executor_openai import _invocation

_BEARER = "sandbox-bearer-for-the-local-llm-proxy-0123456789"


@pytest.fixture
def server():
    fake = FakeOpenAIServer(keys=[_BEARER])
    with fake.running():
        yield fake


@pytest.fixture
def proxied(monkeypatch, server, tmp_path):
    """The custom provider through the local-llm-proxy, as sandbox create sets it up."""
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(eo, "_RETRY_BASE_SECONDS", 0.0)
    monkeypatch.setenv("EMBRY0_LLM_CUSTOM_BASE_URL", server.url)
    monkeypatch.setenv("EMBRY0_LLM_CUSTOM_VIA_PROXY", "true")
    monkeypatch.delenv("EMBRY0_LLM_CUSTOM_API_KEY", raising=False)
    token = tmp_path / "local_llm_proxy_token"
    token.write_text(_BEARER + "\n")
    monkeypatch.setenv("EMBRY0_LLM_CUSTOM_PROXY_TOKEN_PATH", str(token))
    return server


def _run(inv, events: list[dict[str, Any]] | None = None):
    sink = events if events is not None else []
    return asyncio.run(OpenAICompatibleExecutor().run(inv, {"_test_writer": sink.append}))


def test_through_the_proxy_the_run_authenticates_with_its_bearer_file(proxied) -> None:
    proxied.answer(completion(content="done"))
    out = _run(_invocation())
    assert not out.is_error, out.error_message
    assert {r["headers"].get("Authorization") for r in proxied.requests} == {f"Bearer {_BEARER}"}


def test_through_the_proxy_without_a_bearer_nothing_is_sent(proxied, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EMBRY0_LLM_CUSTOM_PROXY_TOKEN_PATH", str(tmp_path / "missing"))
    out = _run(_invocation())
    assert out.is_error and "local-llm-proxy" in (out.error_message or "")
    assert proxied.requests == []


def test_a_conversation_over_the_window_elides_old_tool_output(proxied, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EMBRY0_LLM_CUSTOM_CONTEXT_TOKENS", "4096")
    (tmp_path / "big.txt").write_text("x" * 6_000 + "\n")
    calls = [tool_call(f"call_{i}", "Read", {"file_path": "big.txt"}) for i in range(4)]
    for call in calls:
        proxied.answer(completion(tool_calls=[call], prompt_tokens=0))
    proxied.answer(completion(content="done", prompt_tokens=0))
    # The latest read stays whole; the reads before it are what goes.
    out = _run(_invocation(max_turns=10))
    assert not out.is_error, out.error_message
    last = proxied.chat_requests()[-1]["json"]["messages"]
    elided = [m for m in last if m.get("role") == "tool" and str(m.get("content")).startswith("[earlier tool output")]
    assert elided, "the oldest tool output was elided to fit 4,096 tokens"
    assert last[1]["role"] == "user", "the task is kept whole"


def test_a_conversation_that_cannot_fit_fails_with_its_own_code(proxied, monkeypatch) -> None:
    monkeypatch.setenv("EMBRY0_LLM_CUSTOM_CONTEXT_TOKENS", "1100")
    out = _run(_invocation(prompt="y" * 20_000))
    assert out.is_error
    assert out.error_code == ErrorCode.CONTEXT_WINDOW_EXCEEDED.value
    assert "1100-token context window" in (out.error_message or "")
    assert proxied.chat_requests() == [], "a request the server can only refuse is never sent"


def test_without_a_setting_the_window_comes_from_the_servers_models(proxied, monkeypatch) -> None:
    seen: list[str] = []

    async def window(provider, base_url, api_key, model):
        seen.append(model)
        return 4096

    monkeypatch.setattr(eo, "_context_window", window)
    proxied.answer(completion(content="done"))
    assert not _run(_invocation(model="custom/qwen")).is_error
    assert seen == ["qwen"], "asked for the upstream id, the routing prefix stripped"


def test_the_servers_refusal_of_a_long_request_has_the_same_code() -> None:
    message = (
        "custom answered HTTP 400: This model's maximum context length is 65536 tokens. "
        "However, you requested 70429 tokens (70429 in the messages, 0 in the completion)."
    )
    assert classify_agent_error(message) is ErrorCode.CONTEXT_WINDOW_EXCEEDED, "not a rate limit for its 429"


def test_window_from_models_reads_each_servers_field() -> None:
    assert window_from_models({"data": [{"id": "qwen", "max_model_len": 65536}]}, "qwen") == 65536
    assert window_from_models({"data": [{"id": "llama", "context_length": 8192}]}, "llama") == 8192
    assert window_from_models({"data": [{"id": "x", "context_window": 32000}]}, "x") == 32000
    assert window_from_models({"data": [{"id": "other", "max_model_len": 1}]}, "qwen") is None
    assert window_from_models({"object": "list"}, "qwen") is None


def test_fit_keeps_what_fits_and_keeps_the_task_and_tail_whole() -> None:
    task = {"role": "user", "content": "the task"}
    small = [task, {"role": "assistant", "content": "ok"}]
    assert fit_messages(system="s", tools=[], messages=small, window=4096, model="m") is small

    big_tool = {"role": "tool", "tool_call_id": "c", "content": "z" * 40_000}
    tail = [{"role": "assistant", "content": f"step {i}"} for i in range(6)]
    fitted = fit_messages(system="s", tools=[], messages=[task, big_tool, *tail], window=8192, model="m")
    assert fitted[0] == task and fitted[2:] == tail
    assert fitted[1]["content"].startswith("[earlier tool output elided to fit the 8192-token context window")
    assert fitted[1]["tool_call_id"] == "c", "the result still answers its call"

    with pytest.raises(ContextWindowExceeded):
        fit_messages(system="s" * 40_000, tools=[], messages=[task], window=4096, model="m")


def test_the_ratio_follows_the_servers_count_but_never_turns_reckless() -> None:
    assert calibrated_ratio(1000, 4000, 0.3) == 0.25
    assert calibrated_ratio(0, 4000, 0.3) == 0.3
    assert calibrated_ratio(1, 1_000_000, 0.3) == 0.15
    assert answer_reserve(65536) == 8192 and answer_reserve(4096) == 1024
