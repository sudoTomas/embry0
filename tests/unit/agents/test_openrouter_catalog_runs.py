"""A run on a model of OpenRouter's live catalog: its rates travel to the sandbox, which has no catalog."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

import embry0.agents.executor_openai as eo
from embry0.agents import openrouter_catalog as oc
from embry0.agents.executor_openai import OpenAICompatibleExecutor
from embry0.agents.resolver import resolve_agent_invocation
from embry0.execution.agent_runner import AgentOutput
from embry0.orchestration.nodes.agent import _priced_from_the_catalog
from embry0.safety.error_codes import ErrorCode
from embry0.sandbox.runner import _invocation_from_config
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED
from tests.fixtures.openai_compatible_server import FakeOpenAIServer, completion

OPUS = "openrouter/anthropic/claude-opus-5.5"
RATES = (4.0, 20.0)
_KEY = "sk-or-test-key-not-real-0123456789"


def _catalog() -> oc.Catalog:
    opus = oc.CatalogModel(
        model=OPUS, name="Anthropic: Claude Opus 5.5", input_usd_per_mtok=4.0, output_usd_per_mtok=20.0
    )
    return oc.Catalog(models={OPUS: opus}, fetched_at=datetime(2026, 10, 25, tzinfo=UTC))


@pytest.fixture(autouse=True)
def _openrouter(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "openrouter")
    yield
    oc.install(None)


def _resolve(model: str):
    return resolve_agent_invocation(
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
        job_agent_models={"developer": model},
    )


def test_the_resolver_sends_a_catalog_models_rates_with_the_run() -> None:
    oc.install(_catalog())
    invocation = _resolve(OPUS)
    assert (invocation.model, invocation.provider) == (OPUS, "openrouter")
    assert invocation.pricing_usd_per_mtok == RATES
    assert _resolve("grok-4.7").pricing_usd_per_mtok is None, "the static price table covers it in the sandbox too"


def test_the_sandbox_runner_reads_the_rates_and_ignores_a_bad_value() -> None:
    cfg = {"agent_type": "developer", "prompt": "p", "model": OPUS, "provider": "openrouter"}
    assert _invocation_from_config({**cfg, "pricing_usd_per_mtok": [4.0, 20.0]}).pricing_usd_per_mtok == RATES
    for bad in (None, [4.0], ["x", 1], [-1, 2], "4,20"):
        assert _invocation_from_config({**cfg, "pricing_usd_per_mtok": bad}).pricing_usd_per_mtok is None


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[FakeOpenAIServer]:
    fake = FakeOpenAIServer(keys=[_KEY])
    with fake.running():
        monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("EMBRY0_LLM_OPENROUTER_API_KEY", _KEY)
        monkeypatch.setattr(eo, "_RETRY_BASE_SECONDS", 0.0)
        real = eo._make_client
        monkeypatch.setattr(eo, "_make_client", lambda _base, key: real(fake.url, key))
        yield fake


def _sandbox_run(**kw: Any) -> AgentOutput:
    """The run as the sandbox makes it: no catalog in this process."""
    oc.install(_catalog())
    invocation = dataclasses.replace(_resolve(OPUS), max_turns=3, **kw)
    oc.install(None)
    return asyncio.run(OpenAICompatibleExecutor().run(invocation, {"_test_writer": [].append}))


def test_the_sandbox_prices_the_run_from_the_rates_it_was_sent(server: FakeOpenAIServer) -> None:
    server.answer(completion(content="done", prompt_tokens=1_000_000, completion_tokens=100_000))
    out = _sandbox_run()
    assert not out.is_error, out.error_message
    assert server.chat_requests()[0]["json"]["model"] == "anthropic/claude-opus-5.5"
    assert out.cost_usd == pytest.approx(4.0 + 2.0)


def test_without_the_rates_the_sandbox_refuses_to_meter_it_at_zero(server: FakeOpenAIServer) -> None:
    server.answer(completion(content="done", prompt_tokens=1000, completion_tokens=10))
    out = _sandbox_run(pricing_usd_per_mtok=None)
    assert out.is_error and out.error_code == ErrorCode.INVALID_CONFIG.value
    assert out.error_message.startswith("cost unknown:")


def test_the_orchestrator_prices_a_run_an_older_sandbox_could_not() -> None:
    oc.install(_catalog())
    invocation = _resolve(OPUS)
    stale = AgentOutput(
        agent_type="developer",
        is_error=True,
        error_code=ErrorCode.INVALID_CONFIG.value,
        error_message=f"cost unknown: model {OPUS!r} has no price entry in embry0/agents/providers.py, ...",
        output="done",
        input_tokens=1_000_000,
        output_tokens=100_000,
    )
    priced = _priced_from_the_catalog(stale, invocation, "developer")
    assert (priced.is_error, priced.error_code, priced.error_message, priced.output) == (False, None, "", "done")
    assert priced.cost_usd == pytest.approx(6.0)

    other = dataclasses.replace(stale, error_message="reached max_turns (3)")
    assert _priced_from_the_catalog(other, invocation, "developer") is other, "any other failure stays"
    static = _resolve("grok-4.7")
    assert _priced_from_the_catalog(stale, static, "developer") is stale, "no rates were sent: nothing to price with"
