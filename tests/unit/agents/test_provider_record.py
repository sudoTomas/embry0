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

#: Streams recorded from the sandbox image's CLI (claude-code 2.1.316, Agent SDK 0.2.176) against a fake upstream that
#: answers as Anthropic's API does: each content block arrives as its own assistant message carrying the response's
#: starting usage (``output_tokens: 1``), and the result's ``modelUsage`` counts every call the CLI made.
STREAMS = Path(__file__).parents[2] / "fixtures" / "sdk-streams"


def _recorded(
    name: str,
    *,
    drop: tuple[str, ...] = (),
    without_result: bool = False,
    model_usage: Any = None,
) -> list[Any]:
    """The recorded stream ``name``; its result without the keys ``drop`` names, or with ``model_usage`` in place
    of its own."""
    from claude_agent_sdk._internal.message_parser import parse_message

    messages = []
    for line in (STREAMS / name).read_text().splitlines():
        data = json.loads(line)
        if data.get("type") == "result":
            if without_result:
                continue
            data = {k: v for k, v in data.items() if k not in drop}
            if model_usage is not None:
                data["modelUsage"] = model_usage
        messages.append(parse_message(data))
    return messages


def _result_of(name: str) -> dict[str, Any]:
    return next(json.loads(line) for line in (STREAMS / name).read_text().splitlines() if '"type":"result"' in line)


async def _sdk_run(
    invocation: AgentInvocation,
    messages: list[Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    restored: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> tuple[Any, list[dict[str, Any]], Any]:
    """Run the Agent SDK executor over ``messages``; ``restored`` is the cost-state line a resumed session's file
    ends with, as the CLI wrote it at the earlier run's exit, or each such line the file holds, in order."""
    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("XAI_API_KEY", "console-key")
    monkeypatch.setenv("EMBRY0_XAI_CONSOLE_KEY", "console-key")
    monkeypatch.delenv("EMBRY0_XAI_PROXY_URL", raising=False)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    kwargs: dict[str, Any] = {}
    if restored is not None:
        lines = restored if isinstance(restored, list) else [restored]
        session = str(lines[-1]["sessionId"])
        session_dir = home / ".claude" / "projects" / sanitize_cwd_for_session_dir("/workspace")
        session_dir.mkdir(parents=True)
        (session_dir / f"{session}.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines))
        kwargs["resume_session_id"] = session
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


def _grok_price(input_tokens: int, output_tokens: int, cache_read: int, cache_creation: int) -> float:
    return (input_tokens * 2.0 + output_tokens * 6.0 + cache_read * 2.0 * 0.25 + cache_creation * 2.0 * 1.5) / 1e6


def _grok() -> AgentInvocation:
    return _inv(provider="xai", model="grok-4.7", provider_record=_grok_record())


async def test_a_grok_sdk_run_is_charged_its_calls_real_output_not_the_cli_s_figure(tmp_path, monkeypatch) -> None:
    """Two calls, the first sent as two messages; each message says 1 output token, the calls spent 400."""
    out, events, options = await _sdk_run(_grok(), _recorded("grok-two-calls.jsonl"), tmp_path, monkeypatch)
    assert not out.is_error, out.error_message
    charge = _grok_price(3000, 400, 400, 500)
    assert out.cost_usd == pytest.approx(charge), f"charged {out.cost_usd}; the calls' tokens cost {charge}"
    assert out.cost_usd != pytest.approx(_result_of("grok-two-calls.jsonl")["total_cost_usd"]), "the CLI's figure"
    assert [e["cost_usd"] for e in events if e.get("type") == "cost_update"] == [pytest.approx(charge)]
    assert options.env["ANTHROPIC_BASE_URL"] == "https://grok.example.test", "the CLI was not pointed at the record's"
    assert options.env["ANTHROPIC_API_KEY"] == "console-key"


async def test_a_compaction_s_call_is_charged_too(tmp_path, monkeypatch) -> None:
    """The compaction's call makes no assistant message and is not in the result's usage: only its modelUsage."""
    out, _events, _ = await _sdk_run(_grok(), _recorded("grok-compacted.jsonl"), tmp_path, monkeypatch)
    usage = _result_of("grok-compacted.jsonl")["modelUsage"]["grok-4.7"]
    charge = _grok_price(usage["inputTokens"], usage["outputTokens"], usage["cacheReadInputTokens"], 0)
    assert (usage["inputTokens"], usage["outputTokens"]) == (373500, 2150), "the recording moved"
    assert out.cost_usd == pytest.approx(charge), f"charged {out.cost_usd}, the calls cost {charge}"


async def test_a_resumed_grok_session_is_charged_this_run_s_calls_only(tmp_path, monkeypatch) -> None:
    """The resumed run's modelUsage counts the session's earlier run too, as the CLI restored it from its file."""
    first = _result_of("grok-session-first.jsonl")
    restored = {
        "type": "cost-state",
        "sessionId": _result_of("grok-session-resumed.jsonl")["session_id"],
        "totalCostUSD": first["total_cost_usd"],
        "modelUsage": first["modelUsage"],
    }
    out, _events, _ = await _sdk_run(
        _grok(), _recorded("grok-session-resumed.jsonl"), tmp_path, monkeypatch, restored=restored
    )
    assert _result_of("grok-session-resumed.jsonl")["modelUsage"]["grok-4.7"]["inputTokens"] == 6000, "the recording"
    assert out.cost_usd == pytest.approx(_grok_price(3000, 400, 400, 500))


async def test_a_session_this_cli_did_not_restore_is_charged_its_whole_usage(tmp_path, monkeypatch) -> None:
    """A result's totals below the saved ones: the CLI did not restore them, so its totals are the run's own (#501)."""
    later = _result_of("grok-session-resumed.jsonl")
    restored = {
        "type": "cost-state",
        "sessionId": _result_of("grok-session-first.jsonl")["session_id"],
        "totalCostUSD": later["total_cost_usd"],
        "modelUsage": later["modelUsage"],
    }
    out, _events, _ = await _sdk_run(
        _grok(), _recorded("grok-session-first.jsonl"), tmp_path, monkeypatch, restored=restored
    )
    assert out.cost_usd == pytest.approx(_grok_price(3000, 400, 400, 500))


async def test_a_compacted_run_this_cli_did_not_restore_keeps_its_compaction(tmp_path, monkeypatch) -> None:
    """Totals below the saved ones are the run's own, a compaction's call included: nothing is taken off them."""
    saved = {"type": "cost-state", "sessionId": _result_of("grok-compacted.jsonl")["session_id"]}
    saved["modelUsage"] = {"grok-4.7": {"inputTokens": 400_000, "outputTokens": 10}}
    out, _events, _ = await _sdk_run(_grok(), _recorded("grok-compacted.jsonl"), tmp_path, monkeypatch, restored=saved)
    usage = _result_of("grok-compacted.jsonl")["modelUsage"]["grok-4.7"]
    charge = _grok_price(usage["inputTokens"], usage["outputTokens"], usage["cacheReadInputTokens"], 0)
    assert out.cost_usd == pytest.approx(charge), f"charged {out.cost_usd}, the calls cost {charge}"


def _cost_state(name: str, model_usage: dict[str, Any] | None = None) -> dict[str, Any]:
    """The cost-state line the CLI saves at the exit of the recorded run ``name`` (its result's totals), under the
    resumed recording's session id; ``model_usage`` in place of its totals."""
    result = _result_of(name)
    return {
        "type": "cost-state",
        "sessionId": _result_of("grok-session-resumed.jsonl")["session_id"],
        "totalCostUSD": result["total_cost_usd"],
        "modelUsage": model_usage if model_usage is not None else result["modelUsage"],
    }


async def test_the_last_saved_totals_are_the_restored_ones(tmp_path, monkeypatch) -> None:
    """A session file holds a cost-state line for each run that exited; the CLI restores the last one."""
    earlier = {"grok-4.7": {"inputTokens": 1000, "outputTokens": 100, "cacheReadInputTokens": 0}}
    lines = [_cost_state("grok-session-first.jsonl", earlier), _cost_state("grok-session-first.jsonl")]
    out, _events, _ = await _sdk_run(
        _grok(), _recorded("grok-session-resumed.jsonl"), tmp_path, monkeypatch, restored=lines
    )
    assert out.cost_usd == pytest.approx(_grok_price(3000, 400, 400, 500)), "not the last line's totals taken off"


async def test_every_model_of_the_result_is_counted(tmp_path, monkeypatch) -> None:
    """A run whose calls used two models (the CLI's small model for a title, say): their counts are summed."""
    usage = _result_of("grok-two-calls.jsonl")["modelUsage"]["grok-4.7"]
    small = {"inputTokens": 1000, "outputTokens": 50, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0}
    out, _events, _ = await _sdk_run(
        _grok(),
        _recorded("grok-two-calls.jsonl", model_usage={"grok-4.7": usage, "grok-4.7-mini": small}),
        tmp_path,
        monkeypatch,
    )
    assert out.cost_usd == pytest.approx(_grok_price(4000, 450, 400, 500)), "a model of the result was not counted"


@pytest.mark.parametrize(
    "model_usage",
    [
        {"grok-4.7": {"input_tokens": 3000, "output_tokens": 400}},
        {"grok-4.7": None},
        {"grok-4.7": {"inputTokens": "3000", "outputTokens": "400"}},
    ],
    ids=["renamed keys", "a null entry", "text counts"],
)
async def test_a_model_usage_with_nothing_readable_is_charged_the_result_s_usage(
    tmp_path, monkeypatch, model_usage: dict[str, Any]
) -> None:
    """Never a silent $0 (#219): the result's usage still counts the main loop's calls."""
    out, _events, _ = await _sdk_run(
        _grok(), _recorded("grok-two-calls.jsonl", model_usage=model_usage), tmp_path, monkeypatch
    )
    assert out.cost_usd == pytest.approx(_grok_price(3000, 400, 400, 500)), f"charged {out.cost_usd}"


async def test_a_resumed_run_is_charged_at_least_its_main_loop_s_calls(tmp_path, monkeypatch) -> None:
    """Saved totals that would leave less than the result's usage (a CLI that did not restore them, though its
    own totals happen to be as large) are not all taken off: the result's usage is the floor."""
    larger = {"grok-4.7": {"inputTokens": 5500, "outputTokens": 700, "cacheReadInputTokens": 700}}
    lines = [_cost_state("grok-session-first.jsonl", larger)]
    out, _events, _ = await _sdk_run(
        _grok(), _recorded("grok-session-resumed.jsonl"), tmp_path, monkeypatch, restored=lines
    )
    assert out.cost_usd == pytest.approx(_grok_price(3000, 400, 400, 1000)), f"charged {out.cost_usd}"


async def test_a_result_without_model_usage_is_charged_its_usage(tmp_path, monkeypatch) -> None:
    out, _events, _ = await _sdk_run(
        _grok(), _recorded("grok-two-calls.jsonl", drop=("modelUsage",)), tmp_path, monkeypatch
    )
    assert out.cost_usd == pytest.approx(_grok_price(3000, 400, 400, 500))


async def test_a_run_that_ends_with_no_result_is_charged_the_calls_it_reported(tmp_path, monkeypatch) -> None:
    """A lower bound: each call once by its id, at the usage it started with."""
    out, _events, _ = await _sdk_run(
        _grok(), _recorded("grok-two-calls.jsonl", without_result=True), tmp_path, monkeypatch
    )
    assert out.cost_usd == pytest.approx(_grok_price(3000, 2, 400, 500))


async def test_an_anthropic_run_is_charged_what_its_cli_reports(tmp_path, monkeypatch) -> None:
    """The CLI knows Anthropic's pricing (long context, cache tiers) better than flat rates: its figure stands, and
    the record's rates, resolved from the deployment's catalogue, only estimate the turns."""
    enable_providers(monkeypatch, anthropic={"models": {"claude-sonnet-5": {"prices": [{"in": 6.0, "out": 30.0}]}}})
    record = providers.provider_record("claude-sonnet-5", at=AT)
    catalogue.install(None)  # the sandbox: the shipped catalogue only
    messages = _recorded("grok-two-calls.jsonl")
    for message in messages:
        if hasattr(message, "model"):
            message.model = "claude-sonnet-5"
    out, events, _ = await _sdk_run(_inv(provider_record=record), messages, tmp_path, monkeypatch)
    assert out.cost_usd == pytest.approx(_result_of("grok-two-calls.jsonl")["total_cost_usd"])
    turn = [e for e in events if e.get("type") == "turn_usage"]
    assert turn[0]["cost_usd"] == pytest.approx((1000 * 6.0 + 1 * 30.0 + 400 * 6.0 * 0.1) / 1e6), "the shipped price"


async def test_a_run_from_an_orchestrator_without_records_is_charged_as_before(tmp_path, monkeypatch) -> None:
    out, _events, options = await _sdk_run(
        _inv(provider="xai", model="grok-4.7"), _recorded("grok-two-calls.jsonl"), tmp_path, monkeypatch
    )
    assert out.cost_usd == pytest.approx(_result_of("grok-two-calls.jsonl")["total_cost_usd"])
    assert options.env["ANTHROPIC_BASE_URL"] == "https://api.x.ai", "the sandbox's own table"


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


def test_the_record_s_rates_are_those_of_its_moment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dated price: the record resolved for a run takes the price in force at the run's start, Anthropic's too."""
    dated = {"prices": [{"in": 1.0, "out": 1.0}, {"from": "2027-01-01", "in": 9.0, "out": 9.0}]}
    from datetime import date

    def dated_of(spec: dict[str, Any]) -> dict[str, Any]:
        prices = [dict(p) for p in spec["prices"]]
        prices[1]["from"] = date.fromisoformat(prices[1]["from"])
        return {"prices": prices}

    enable_providers(
        monkeypatch,
        "deepseek",
        deepseek={"models": {"deepseek-flash": dated_of(dated)}},
        anthropic={"models": {"claude-sonnet-5": dated_of(dated)}},
    )
    before, after = datetime(2026, 12, 31, tzinfo=UTC), datetime(2027, 1, 2, tzinfo=UTC)
    for model in ("deepseek-flash", "claude-sonnet-5"):
        assert providers.provider_record(model, at=before).rates == (1.0, 1.0), model
        assert providers.provider_record(model, at=after).rates == (9.0, 9.0), f"{model}: not the run's day's price"


def test_one_catalogue_routes_the_model_and_makes_its_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """Another process's write installed between two reads cannot route a model by one catalogue and hand its run
    the other's record."""
    from embry0.agents.resolver import resolve_agent_invocation
    from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

    on = enable_providers(monkeypatch, "deepseek")
    off = catalogue.seed()
    reads = {"n": 0}

    def installed() -> catalogue.Catalogue:
        reads["n"] += 1
        return on if reads["n"] == 1 else off

    monkeypatch.setattr(catalogue, "current", installed)
    monkeypatch.setattr(providers, "current", installed)
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
    record = invocation.provider_record
    assert record is not None and (invocation.provider, record.name) == ("deepseek", "deepseek"), (
        f"routed to {invocation.provider!r}, recorded {record and record.name!r}"
    )


def test_a_custom_model_s_window_survives_the_runner_s_config() -> None:
    """#174: the catalogue's context_window rides the record into the sandbox; without one the executor asks the
    server."""
    record = _record(name="custom", context_window=65536)
    config = json.loads(json.dumps({"agent_type": "developer", "prompt": "p", "provider_record": record.to_config()}))
    carried = _invocation_from_config(config).provider_record
    assert carried is not None and carried.context_window == 65536, f"the window was lost on the way: {carried}"
    assert _record().to_config().get("context_window", "absent") is None


@pytest.mark.parametrize("window", [0, -1, True, "65536", 1.5])
def test_a_record_with_a_window_that_is_not_a_count_is_none(window: Any) -> None:
    assert ProviderRecord.from_config({**_record().to_config(), "context_window": window}) is None
