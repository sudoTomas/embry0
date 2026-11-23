"""Each run carries its provider's record (#173 PR 3, ADR 0031): resolved by the orchestrator from the deployment's
catalogue at the run's start, read by the executors instead of the sandbox's own table, and what a run is charged."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

import embry0.agents.executor_openai as eo
from embry0.agents import catalogue, providers
from embry0.agents.claude_cli_session import sanitize_cwd_for_session_dir
from embry0.agents.executor import SdkAgentExecutor
from embry0.agents.executor_factory import select_executor
from embry0.agents.executor_openai import OpenAICompatibleExecutor
from embry0.agents.invocation import AgentInvocation, ProviderRecord
from embry0.safety.policy import default_policy_for_agent
from embry0.sandbox.runner import _invocation_from_config
from tests.fixtures.openai_compatible_server import FakeOpenAIServer, completion
from tests.fixtures.provider_catalogue import enable_providers

_KEY = "sk-test-key-not-real-0123456789"
AT = datetime(2026, 11, 24, 9, tzinfo=UTC)


def _inv(**kw: Any) -> AgentInvocation:
    base: dict[str, Any] = {
        "agent_type": "developer",
        "prompt": "do the task",
        "system_prompt": "",
        "system_context": "",
        "model": "claude-sonnet-5",
        "tools": ["Read"],
        "skills": [],
        "mcp_servers": {},
        "max_turns": 5,
        "timeout_seconds": 30,
        "execution_mode": "sdk",
        "auth_mode": "oauth",
        "safety_policy": default_policy_for_agent("developer"),
        "channel_config": None,
    }
    base.update(kw)
    return AgentInvocation(**base)


def _record(**kw: Any) -> ProviderRecord:
    base: dict[str, Any] = {
        "name": "deepseek",
        "runtime": "chat_completions",
        "upstream_model": "deepseek-flash",
        "rates": (4.0, 8.0),
        "cache_read_factor": 0.5,
        "cache_write_factor": 2.0,
        "api_root": "https://api.deepseek.com",
        "key_env": "EMBRY0_LLM_DEEPSEEK_API_KEY",
        "resolved_at": AT.isoformat(),
    }
    base.update(kw)
    return ProviderRecord(**base)


# ---- the record ----


def test_the_record_is_the_installed_catalogue_s(monkeypatch: pytest.MonkeyPatch) -> None:
    """Built by the orchestrator from the deployment's catalogue, not the shipped one."""
    enable_providers(
        monkeypatch,
        "deepseek",
        deepseek={
            "models": {"deepseek-flash": {"prices": [{"in": 0.5, "out": 2.5}], "request_extras": {"top_p": 0.9}}}
        },
    )
    record = providers.provider_record("deepseek-flash", at=AT)
    assert record == _record(
        rates=(0.5, 2.5),
        cache_read_factor=providers.CACHE_READ_FACTOR,
        cache_write_factor=providers.CACHE_WRITE_FACTOR,
        request_extras={"top_p": 0.9},
    )
    assert record.charged_from == "catalogue"


def test_each_kind_of_provider_has_what_its_executor_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    enable_providers(monkeypatch, "custom", "openrouter")
    xai = providers.provider_record("grok-4.7", at=AT)
    assert (xai.runtime, xai.base_url, xai.key_env, xai.rates, xai.charged_from) == (
        "sdk",
        "https://api.x.ai",
        "XAI_API_KEY",
        (2.0, 6.0),
        "catalogue",
    )
    custom = providers.provider_record("custom/llama3.3:70b", at=AT)
    assert (custom.upstream_model, custom.api_root, custom.base_url_env, custom.key_env) == (
        "llama3.3:70b",
        "",
        "EMBRY0_LLM_CUSTOM_BASE_URL",
        "EMBRY0_LLM_CUSTOM_API_KEY",
    )
    routed = providers.provider_record("openrouter/openai/gpt-6.1-sol", at=AT)
    assert (routed.upstream_model, routed.api_root) == ("openai/gpt-6.1-sol", "https://openrouter.ai/api/v1")
    anthropic = providers.provider_record("claude-sonnet-5", at=AT)
    assert (anthropic.name, anthropic.charged_from, anthropic.rates, anthropic.base_url, anthropic.key_env) == (
        "anthropic",
        "cli",
        (3.0, 15.0),
        "",
        "",
    )


def test_a_withheld_price_reaches_the_record_as_none() -> None:
    withheld = catalogue.Catalogue.build(catalogue.seed().document, withheld=frozenset({"grok-4.7"}))
    assert providers.provider_record("grok-4.7", withheld).rates is None


def test_the_record_survives_the_runner_s_config() -> None:
    record = _record(request_extras={"reasoning_effort": "none"})
    config = json.loads(json.dumps({"agent_type": "developer", "prompt": "p", "provider_record": record.to_config()}))
    assert _invocation_from_config(config).provider_record == record
    assert _invocation_from_config({"agent_type": "developer", "prompt": "p"}).provider_record is None, (
        "an orchestrator that predates records sends none: the runner reads its table"
    )


@pytest.mark.parametrize(
    "broken",
    [
        {"runtime": "rpc"},
        {"rates": [1.0]},
        {"rates": [-1.0, 1.0]},
        {"rates": ["1", 2]},
        {"cache_read_factor": float("nan")},
        {"cache_write_factor": True},
        {"name": ""},
        {"key_env": 5},
        {"request_extras": ["x"]},
    ],
)
def test_a_record_that_is_not_one_is_none(broken: dict[str, Any]) -> None:
    assert ProviderRecord.from_config({**_record().to_config(), **broken}) is None


def test_the_trace_keeps_the_pricing_and_nothing_of_the_address_or_key() -> None:
    assert _record().trace_pricing() == {
        "provider": "deepseek",
        "model": "deepseek-flash",
        "rates": {"in": 4.0, "out": 8.0},
        "cache": {"read": 0.5, "write": 2.0},
        "resolved_at": AT.isoformat(),
        "charged_from": "catalogue",
    }


def test_the_resolver_hands_the_run_its_record(monkeypatch: pytest.MonkeyPatch) -> None:
    from embry0.agents.resolver import resolve_agent_invocation
    from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

    enable_providers(
        monkeypatch, "deepseek", deepseek={"models": {"deepseek-flash": {"prices": [{"in": 0.5, "out": 2.5}]}}}
    )
    invocation = resolve_agent_invocation(
        agent_type="developer",
        prompt="p",
        system_context="",
        global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
        repo_prefs=None,
        job_overrides={},
        agent_definition=dict(BUILTIN_SEED["developer"]),
        pipeline_config={},
        max_turns=10,
        timeout_seconds=600,
        credentials={"api_key": "", "oauth_token": "tok"},
        job_agent_models={"developer": "deepseek-flash"},
    )
    assert invocation.provider_record is not None and invocation.provider_record.rates == (0.5, 2.5)
    assert invocation.pricing_usd_per_mtok == (0.5, 2.5), "an older sandbox image reads the rates alone"


# ---- the executors read it ----


def test_the_executor_is_chosen_by_the_record_s_runtime() -> None:
    """A provider the sandbox's table does not know still runs, as its record says."""
    record = _record(name="newcomer", upstream_model="nc-1")
    chosen = select_executor(_inv(provider="newcomer", model="nc-1", provider_record=record))
    assert isinstance(chosen, OpenAICompatibleExecutor)


@pytest.fixture
def server():
    fake = FakeOpenAIServer(keys=[_KEY])
    with fake.running():
        yield fake


def test_a_chat_completions_run_calls_and_prices_as_its_record_says(
    server: FakeOpenAIServer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Endpoint, key variable, upstream id, extras and price from the record; nothing from the shipped table."""
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("EMBRY0_LLM_NEWCOMER_KEY", _KEY)
    called: list[str] = []
    real = eo._make_client

    def make(base_url: str, api_key: str) -> Any:
        called.append(base_url)
        return real(server.url, api_key)

    monkeypatch.setattr(eo, "_make_client", make)
    server.answer(completion(content="done", prompt_tokens=1000, cached_tokens=200, completion_tokens=50))
    record = _record(
        name="newcomer",
        upstream_model="nc-1-upstream",
        api_root="https://newcomer.example.test/v1",
        key_env="EMBRY0_LLM_NEWCOMER_KEY",
        request_extras={"temperature": 0.2},
    )
    events: list[dict[str, Any]] = []
    out = asyncio.run(
        OpenAICompatibleExecutor().run(
            _inv(provider="newcomer", model="newcomer/nc-1", tools=[], provider_record=record),
            {"_test_writer": events.append},
        )
    )
    assert not out.is_error, out.error_message
    assert called == ["https://newcomer.example.test/v1"]
    body = server.chat_requests()[0]["json"]
    assert (body["model"], body["temperature"]) == ("nc-1-upstream", 0.2)
    expected = (800 * 4.0 + 200 * 4.0 * 0.5 + 50 * 8.0) / 1_000_000
    assert out.cost_usd == pytest.approx(expected), "not the record's rates and cache factors"
    turn = [e for e in events if e.get("type") == "turn_usage"]
    assert turn and turn[0]["cost_usd"] == pytest.approx(expected)


def test_a_record_without_rates_leaves_the_run_unpriced(
    server: FakeOpenAIServer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Never the sandbox's own price in place of the catalogue's absent one."""
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("EMBRY0_LLM_DEEPSEEK_API_KEY", _KEY)
    real = eo._make_client
    monkeypatch.setattr(eo, "_make_client", lambda base_url, api_key: real(server.url, api_key))
    server.answer(completion(content="done"))
    out = asyncio.run(
        OpenAICompatibleExecutor().run(
            _inv(provider="deepseek", model="deepseek-flash", tools=[], provider_record=_record(rates=None)),
            {"_test_writer": lambda e: None},
        )
    )
    assert out.is_error and out.error_message.startswith("cost unknown:"), out.error_message


# ---- an Agent SDK run's charge ----

SESSION = "2cde0b11-1cda-47f6-8a45-fb99f2126f24"


def _assistant(message_id: str, usage: dict[str, int], model: str = "grok-4.7") -> AssistantMessage:
    return AssistantMessage(
        content=[TextBlock(text="x")], model=model, usage=usage, message_id=message_id, session_id=SESSION
    )


def _result(total_cost_usd: float, usage: dict[str, int]) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=2,
        session_id=SESSION,
        total_cost_usd=total_cost_usd,
        usage=usage,
        result="done",
    )


#: Two API calls; the first's usage sent again with its second content block, as the CLI does.
CALLS = [
    _assistant("msg-1", {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 400}),
    _assistant("msg-1", {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 400}),
    _assistant("msg-2", {"input_tokens": 2000, "output_tokens": 300, "cache_creation_input_tokens": 500}),
]
RESULT_USAGE = {"input_tokens": 3000, "output_tokens": 400}


async def _sdk_run(
    invocation: AgentInvocation,
    messages: list[Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    resume: float | None = None,
) -> tuple[Any, list[dict[str, Any]], Any]:
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("XAI_API_KEY", "console-key")
    monkeypatch.setenv("EMBRY0_XAI_CONSOLE_KEY", "console-key")
    monkeypatch.delenv("EMBRY0_XAI_PROXY_URL", raising=False)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    kwargs: dict[str, Any] = {}
    if resume is not None:
        session_dir = home / ".claude" / "projects" / sanitize_cwd_for_session_dir("/workspace")
        session_dir.mkdir(parents=True)
        (session_dir / f"{SESSION}.jsonl").write_text(
            json.dumps({"type": "cost-state", "sessionId": SESSION, "totalCostUSD": resume}) + "\n"
        )
        kwargs["resume_session_id"] = SESSION
    events: list[dict[str, Any]] = []
    captured: dict[str, Any] = {}

    async def stream() -> Any:
        for message in messages:
            yield message

    def fake_query(*, prompt: Any, options: Any) -> Any:
        captured["options"] = options
        return stream()

    with patch("claude_agent_sdk.query", side_effect=fake_query):
        out = await SdkAgentExecutor().run(invocation, {"configurable": {}, "_test_writer": events.append}, **kwargs)
    return out, events, captured.get("options")


def _grok_record(**kw: Any) -> ProviderRecord:
    return _record(
        name="xai",
        runtime="sdk",
        upstream_model="grok-4.7",
        rates=(2.0, 6.0),
        cache_read_factor=0.25,
        cache_write_factor=1.5,
        api_root="",
        base_url="https://grok.example.test",
        key_env="EMBRY0_XAI_CONSOLE_KEY",
        **kw,
    )


#: msg-1 once and msg-2, at the grok record's rates and cache factors.
GROK_CHARGE = ((1000 + 2000) * 2.0 + (100 + 300) * 6.0 + 400 * 2.0 * 0.25 + 500 * 2.0 * 1.5) / 1_000_000


async def test_a_grok_sdk_run_is_charged_from_the_catalogue_not_the_cli(tmp_path, monkeypatch) -> None:
    """The CLI prices grok by its own table of Claude models; the charge is the record's rates on each call's tokens,
    a call's usage sent twice counted once."""
    invocation = _inv(provider="xai", model="grok-4.7", provider_record=_grok_record())
    out, events, options = await _sdk_run(invocation, [*CALLS, _result(0.777, RESULT_USAGE)], tmp_path, monkeypatch)
    assert not out.is_error, out.error_message
    assert out.cost_usd == pytest.approx(GROK_CHARGE), f"charged {out.cost_usd}, the CLI said 0.777"
    assert [e["cost_usd"] for e in events if e.get("type") == "cost_update"] == [pytest.approx(GROK_CHARGE)]
    assert options.env["ANTHROPIC_BASE_URL"] == "https://grok.example.test", "the CLI was not pointed at the record's"
    assert options.env["ANTHROPIC_API_KEY"] == "console-key"


async def test_a_resumed_grok_run_is_charged_its_own_calls_only(tmp_path, monkeypatch) -> None:
    invocation = _inv(provider="xai", model="grok-4.7", provider_record=_grok_record())
    out, _events, _options = await _sdk_run(
        invocation, [*CALLS, _result(1.5, RESULT_USAGE)], tmp_path, monkeypatch, resume=0.723
    )
    assert out.cost_usd == pytest.approx(GROK_CHARGE)


async def test_an_anthropic_run_is_charged_what_its_cli_reports(tmp_path, monkeypatch) -> None:
    """The CLI knows Anthropic's pricing (long context, cache tiers) better than flat rates: its figure stands, and
    the record's rates only estimate the turns."""
    enable_providers(monkeypatch, anthropic={"models": {"claude-sonnet-5": {"prices": [{"in": 6.0, "out": 30.0}]}}})
    record = providers.provider_record("claude-sonnet-5", at=AT)
    catalogue.install(None)  # the sandbox: the shipped catalogue only
    calls = [_assistant("msg-1", {"input_tokens": 1000, "output_tokens": 100}, model="claude-sonnet-5")]
    out, events, _ = await _sdk_run(
        _inv(provider_record=record), [*calls, _result(0.0123, RESULT_USAGE)], tmp_path, monkeypatch
    )
    assert out.cost_usd == pytest.approx(0.0123)
    turn = [e for e in events if e.get("type") == "turn_usage"]
    assert turn[0]["cost_usd"] == pytest.approx((1000 * 6.0 + 100 * 30.0) / 1_000_000), "estimated at the shipped price"


async def test_a_run_from_an_orchestrator_without_records_is_charged_as_before(tmp_path, monkeypatch) -> None:
    out, _events, options = await _sdk_run(
        _inv(provider="xai", model="grok-4.7"), [*CALLS, _result(0.777, RESULT_USAGE)], tmp_path, monkeypatch
    )
    assert out.cost_usd == pytest.approx(0.777)
    assert options.env["ANTHROPIC_BASE_URL"] == "https://api.x.ai", "the sandbox's own table"


async def test_a_grok_run_with_no_call_usage_is_charged_from_the_result_s(tmp_path, monkeypatch) -> None:
    invocation = _inv(provider="xai", model="grok-4.7", provider_record=_grok_record())
    out, _events, _ = await _sdk_run(
        invocation, [_result(0.9, {"input_tokens": 1000, "output_tokens": 10})], tmp_path, monkeypatch
    )
    assert out.cost_usd == pytest.approx((1000 * 2.0 + 10 * 6.0) / 1_000_000)


def test_a_run_resolved_in_the_orchestrator_sends_the_extras_the_deployment_set(
    server: FakeOpenAIServer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """From the resolver to the wire, the sandbox holding only the shipped catalogue."""
    from embry0.agents.resolver import resolve_agent_invocation
    from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("EMBRY0_LLM_DEEPSEEK_API_KEY", _KEY)
    enable_providers(
        monkeypatch, "deepseek", deepseek={"models": {"deepseek-flash": {"request_extras": {"temperature": 0.2}}}}
    )
    invocation = resolve_agent_invocation(
        agent_type="developer",
        prompt="p",
        system_context="",
        global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
        repo_prefs=None,
        job_overrides={},
        agent_definition=dict(BUILTIN_SEED["developer"]),
        pipeline_config={},
        max_turns=3,
        timeout_seconds=60,
        credentials={"api_key": "", "oauth_token": "tok"},
        job_agent_models={"developer": "deepseek-flash"},
    )
    catalogue.install(None)  # the sandbox: the shipped catalogue only
    real = eo._make_client
    monkeypatch.setattr(eo, "_make_client", lambda base_url, api_key: real(server.url, api_key))
    server.answer(completion(content="done"))
    asyncio.run(OpenAICompatibleExecutor().run(invocation, {"_test_writer": lambda e: None}))
    assert server.chat_requests()[0]["json"].get("temperature") == 0.2, "the sandbox sent its shipped extras"
