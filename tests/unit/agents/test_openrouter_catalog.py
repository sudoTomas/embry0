"""OpenRouter's live model list: what is kept, at what price, and how providers.py reads it."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from embry0.agents import openrouter_catalog as oc
from embry0.agents.providers import (
    OPENROUTER,
    known_models,
    model_rates,
    price_usage,
    provider_models,
    unpriced_models,
)

OPUS = "openrouter/anthropic/claude-opus-5.5"


def _entry(model_id: str, prompt: Any, completion: Any, *, tools: bool = True, **extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": model_id,
        "name": extra.pop("name", model_id),
        "supported_parameters": ["tools", "temperature"] if tools else ["temperature"],
        "pricing": {"prompt": prompt, "completion": completion, **extra.pop("pricing", {})},
        "context_length": extra.pop("context_length", 200000),
        "top_provider": {"max_completion_tokens": extra.pop("max_completion_tokens", 64000)},
    }
    entry.update(extra)
    return entry


PAYLOAD: dict[str, Any] = {
    "data": [
        _entry("anthropic/claude-opus-5.5", "0.000004", "0.00002", name="Anthropic: Claude Opus 5.5"),
        _entry(
            "openai/gpt-6.1-sol",
            "0.000002",
            "0.00001",
            pricing={"overrides": [{"min_prompt_tokens": 272000, "prompt": "0.000004", "completion": "0.000015"}]},
        ),
        _entry("deepseek/thinker", "0.0000005", "0.000001", pricing={"internal_reasoning": "0.000003"}),
        _entry("mistralai/chatty", "0.000001", "0.000002", tools=False),
        _entry("anthropic/claude-opus-5.5:batch", "0.000002", "0.00001"),
        _entry("qwen/coder:free", "0", "0"),
        _entry("perplexity/sonar:online", "0.000001", "0.000001"),
        _entry("openrouter/auto", "-1", "-1"),
        _entry("openrouter/free", "0", "0"),
        _entry("vendor/no-price", None, None),
        {"name": "no id at all"},
        _entry("noslash", "0.000001", "0.000001"),
    ]
}


@pytest.fixture(autouse=True)
def _no_catalog_after() -> Iterator[None]:
    yield
    oc.install(None)


@pytest.fixture
def openrouter_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "openrouter")


def _install(payload: dict[str, Any] = PAYLOAD) -> list[oc.CatalogModel]:
    models, _ = oc.parse(payload)
    oc.install(oc.Catalog(models={m.model: m for m in models}, fetched_at=datetime(2026, 10, 25, tzinfo=UTC)))
    return models


def test_only_models_with_tool_calling_at_a_price_are_kept() -> None:
    models, skipped = oc.parse(PAYLOAD)
    assert [m.model for m in models] == [
        OPUS,
        "openrouter/deepseek/thinker",
        "openrouter/openai/gpt-6.1-sol",
    ], "routed ids, sorted"
    assert skipped == {
        "no_tools": 1,
        "batch": 1,
        "free": 1,
        "online": 1,
        "variable_price": 1,
        "unpriced": 2,
        "malformed": 2,
    }


def test_prices_are_per_million_tokens_and_the_highest_tier_counts() -> None:
    by_id = {m.model: m for m in oc.parse(PAYLOAD)[0]}
    opus = by_id[OPUS]
    assert (opus.input_usd_per_mtok, opus.output_usd_per_mtok) == (4.0, 20.0)
    assert (opus.name, opus.vendor, opus.context_length, opus.max_output_tokens) == (
        "Anthropic: Claude Opus 5.5",
        "anthropic",
        200000,
        64000,
    )
    sol = by_id["openrouter/openai/gpt-6.1-sol"]
    assert (sol.input_usd_per_mtok, sol.output_usd_per_mtok) == (4.0, 15.0), "the long-prompt tier is the ceiling"
    thinker = by_id["openrouter/deepseek/thinker"]
    assert thinker.output_usd_per_mtok == 3.0, "reasoning tokens bill as output, at their own higher rate"


def test_an_answer_that_is_not_the_list_is_refused() -> None:
    with pytest.raises(ValueError, match="data"):
        oc.parse({"error": "rate limited"})
    with pytest.raises(ValueError):
        oc.parse([])


def test_a_stored_entry_round_trips_and_a_bad_one_is_dropped() -> None:
    opus = oc.parse(PAYLOAD)[0][0]
    assert oc.CatalogModel.from_json(opus.to_json()) == opus
    assert oc.CatalogModel.from_json({"model": OPUS}) is None
    assert oc.CatalogModel.from_json({**opus.to_json(), "model": "anthropic/claude-opus-5.5"}) is None, "unrouted"
    assert oc.CatalogModel.from_json({**opus.to_json(), "input_usd_per_mtok": -1}) is None


def test_without_a_catalog_openrouter_offers_its_static_entry(openrouter_on: None) -> None:
    assert oc.current() is None
    assert provider_models(OPENROUTER) == OPENROUTER.models
    assert model_rates(OPUS) is None
    assert unpriced_models([OPUS]) == [OPUS], "a model OpenRouter never listed is refused, never run at $0"


def test_the_catalog_replaces_the_static_entry_everywhere(openrouter_on: None) -> None:
    _install()
    assert provider_models(OPENROUTER) == (OPUS, "openrouter/deepseek/thinker", "openrouter/openai/gpt-6.1-sol")
    assert {OPUS, "openrouter/deepseek/thinker"} <= known_models()
    assert model_rates(OPUS) == (4.0, 20.0)
    assert model_rates("openrouter/openai/gpt-6.1-sol") == (4.0, 15.0), "the live price wins over the static one"
    assert unpriced_models([OPUS, "openrouter/vendor/not-listed"]) == ["openrouter/vendor/not-listed"]
    cost = price_usage(OPUS, input_tokens=1_000_000, output_tokens=100_000)
    assert cost == pytest.approx(4.0 + 2.0)


def test_rates_sent_with_a_run_price_it_without_a_catalog() -> None:
    """The sandbox has no catalog: the run's rates arrive on its invocation."""
    assert price_usage(OPUS, input_tokens=1_000_000, output_tokens=0) is None
    assert price_usage(OPUS, input_tokens=1_000_000, output_tokens=0, rates=(4.0, 20.0)) == pytest.approx(4.0)
