"""The provider catalogue (#173, ADR 0031): its schema, the seed embry0 ships, an overlay merged field by field,
and ``embry0.agents.providers`` answering from the catalogue installed in the process."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest

from embry0.agents import catalogue, providers
from embry0.agents.catalogue import Catalogue, CatalogueInvalid, ModelProvider, merge, parse

# ---- the seed is the table it replaced ----

#: The provider constants of ``embry0/agents/providers.py`` before the catalogue: what the seed must still build.
_TABLE_IT_REPLACED = (
    ModelProvider(
        name="anthropic",
        base_url="https://api.anthropic.com",
        api_key_env="ANTHROPIC_API_KEY",
        model_prefixes=("claude-",),
        models=("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"),
        pricing_usd_per_mtok={
            "claude-opus-5": (5.0, 25.0),
            "claude-sonnet-5": (3.0, 15.0),
            "claude-haiku-4-5": (1.0, 5.0),
        },
    ),
    ModelProvider(
        name="xai",
        base_url="https://api.x.ai",
        api_key_env="XAI_API_KEY",
        model_prefixes=("grok-",),
        models=("grok-4.5", "grok-4.7"),
        pricing_usd_per_mtok={"grok-4.5": (2.0, 6.0), "grok-4.7": (2.0, 6.0)},
        turn_budget_factor=2.0,
        timeout_factor=2.0,
        openai_base_url="https://api.x.ai/v1",
    ),
    ModelProvider(
        name="openai",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        model_prefixes=("gpt-",),
        models=("gpt-6.1-sol", "gpt-6-luna"),
        pricing_usd_per_mtok={"gpt-6.1-sol": (2.0, 10.0), "gpt-6-luna": (0.1, 0.5)},
        openai_base_url="https://api.openai.com/v1",
        runtime="chat_completions",
        sandbox_key_env="EMBRY0_LLM_OPENAI_API_KEY",
        chat_only_models=frozenset({"gpt-6.1-sol"}),
        request_extras={"gpt-6-luna": {"reasoning_effort": "none"}},
    ),
    ModelProvider(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        model_prefixes=("openrouter/",),
        models=("openrouter/openai/gpt-6.1-sol",),
        pricing_usd_per_mtok={"openrouter/openai/gpt-6.1-sol": (2.0, 10.0)},
        openai_base_url="https://openrouter.ai/api/v1",
        runtime="chat_completions",
        sandbox_key_env="EMBRY0_LLM_OPENROUTER_API_KEY",
        strip_routing_prefix=True,
        probe_path="/key",
    ),
    ModelProvider(
        name="google",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        api_key_env="GEMINI_API_KEY",
        model_prefixes=("gemini-",),
        models=("gemini-3.8-flash", "gemini-4-argon"),
        pricing_usd_per_mtok={"gemini-3.8-flash": (0.75, 3.75), "gemini-4-argon": (2.0, 10.0)},
        openai_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        runtime="chat_completions",
        sandbox_key_env="EMBRY0_LLM_GOOGLE_API_KEY",
    ),
    ModelProvider(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        model_prefixes=("groq/",),
        models=("groq/openai/gpt-oss-120b", "groq/openai/gpt-oss-20b"),
        pricing_usd_per_mtok={"groq/openai/gpt-oss-120b": (0.15, 0.6), "groq/openai/gpt-oss-20b": (0.075, 0.3)},
        openai_base_url="https://api.groq.com/openai/v1",
        runtime="chat_completions",
        sandbox_key_env="EMBRY0_LLM_GROQ_API_KEY",
        strip_routing_prefix=True,
    ),
    ModelProvider(
        name="deepseek",
        base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        model_prefixes=("deepseek-",),
        models=("deepseek-flash", "deepseek-v4-pro"),
        pricing_usd_per_mtok={"deepseek-flash": (0.3, 1.2), "deepseek-v4-pro": (1.32, 3.96)},
        openai_base_url="https://api.deepseek.com",
        runtime="chat_completions",
        sandbox_key_env="EMBRY0_LLM_DEEPSEEK_API_KEY",
    ),
    ModelProvider(
        name="custom",
        base_url="",
        api_key_env="CUSTOM_LLM_API_KEY",
        model_prefixes=("custom/",),
        models=(),
        pricing_usd_per_mtok={},
        turn_budget_factor=2.0,
        timeout_factor=2.0,
        runtime="chat_completions",
        sandbox_key_env="EMBRY0_LLM_CUSTOM_API_KEY",
        base_url_env="CUSTOM_LLM_BASE_URL",
        sandbox_base_url_env="EMBRY0_LLM_CUSTOM_BASE_URL",
        strip_routing_prefix=True,
        default_pricing_usd_per_mtok=(0.0, 0.0),
    ),
)


def test_the_shipped_catalogue_builds_the_table_it_replaced() -> None:
    assert catalogue.seed().providers == _TABLE_IT_REPLACED, "the seed (providers.yaml) no longer builds the old table"
    assert providers.ALL_PROVIDERS == _TABLE_IT_REPLACED, "the module's providers are not the seed's, in its order"
    assert [p.name for p in providers.PROVIDERS] == ["xai"]
    assert [p.name for p in providers.CATALOG_PROVIDERS] == ["anthropic", "xai"]
    assert [p.name for p in providers.OPENAI_COMPATIBLE] == [
        "openai",
        "openrouter",
        "google",
        "groq",
        "deepseek",
        "custom",
    ]


def test_the_module_answers_from_the_seed_until_a_catalogue_is_installed() -> None:
    assert catalogue.current() is catalogue.seed()
    assert providers.provider_named("xai") is providers.XAI
    assert providers.provider_for_model("grok-4.7") is providers.XAI


# ---- installing a catalogue ----


@pytest.fixture
def installed() -> Iterator[None]:
    try:
        yield
    finally:
        catalogue.install(None)


def _install(overlay: dict[str, Any]) -> Catalogue:
    built = Catalogue.build(merge(catalogue.seed().document, overlay))
    catalogue.install(built)
    return built


def test_providers_answers_from_the_installed_catalogue(installed: None) -> None:
    _install(
        {
            "providers": {
                "openai": {
                    "enabled": True,
                    "factors": {"turns": 3.0},
                    "models": {"gpt-6-luna": {"prices": [{"in": 0.2, "out": 0.8}]}, "gpt-7": {"free": True}},
                },
                "google": {"models": {"gemini-4-argon": None}},
            }
        }
    )
    assert [p.name for p in providers.routed_providers({})] == ["xai", "openai"], "an enabled provider is not routed"
    assert [p.name for p in providers.catalog_providers({})] == ["anthropic", "xai", "openai"]
    assert providers.model_rates("gpt-6-luna") == (0.2, 0.8), "the installed price is not the one read"
    assert providers.model_rates("gpt-7") == (0.0, 0.0), "a free model is not priced at $0"
    assert "gpt-7" in providers.known_models()
    openai = providers.provider_named("openai")
    assert openai is not None and (openai.turn_budget_factor, openai.timeout_factor) == (3.0, 1.0)
    assert providers.model_rates("gemini-4-argon") is None, "a model taken out is still priced"
    catalogue.install(None)
    assert providers.routed_providers({}) == providers.PROVIDERS, "uninstalling does not return to the seed"
    assert providers.model_rates("gpt-6-luna") == (0.1, 0.5)


def test_the_enabling_variable_still_turns_a_provider_on(installed: None) -> None:
    _install({"providers": {"groq": {"enabled": True}}})
    routed = [p.name for p in providers.routed_providers({"OPENAI_COMPATIBLE_PROVIDERS": "deepseek"})]
    assert routed == ["xai", "groq", "deepseek"], "the catalogue's flag and the variable are not both read"


# ---- dated prices ----

_DATED = {
    "providers": {
        "google": {
            "models": {
                "gemini-4-argon": {
                    "prices": [{"in": 2.0, "out": 10.0}, {"from": date(2026, 12, 1), "in": 4.0, "out": 20.0}]
                },
                "gemini-later": {"prices": [{"from": date(2027, 1, 1), "in": 1.5, "out": 7.5}]},
            }
        }
    }
}


def test_a_dated_price_takes_effect_on_its_day() -> None:
    built = Catalogue.build(merge(catalogue.seed().document, _DATED))
    before, on = datetime(2026, 11, 30, 23, 59, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC)
    assert built.rates("gemini-4-argon", at=before) == (2.0, 10.0), "the undated price does not apply before the date"
    assert built.rates("gemini-4-argon", at=on) == (4.0, 20.0), "the dated price does not apply from its day"
    assert built.rates("gemini-later", at=on) is None, "a model priced only from a later day has a price already"
    assert built.rates("gemini-later", at=datetime(2027, 1, 1, tzinfo=UTC)) == (1.5, 7.5)


def test_model_rates_reads_the_price_in_force_now(installed: None) -> None:
    _install(
        {
            "providers": {
                "google": {
                    "models": {
                        "gemini-4-argon": {
                            "prices": [{"in": 2.0, "out": 10.0}, {"from": date(2000, 1, 1), "in": 4.0, "out": 20.0}]
                        }
                    }
                }
            }
        }
    )
    assert providers.model_rates("gemini-4-argon") == (4.0, 20.0)


# ---- merging an overlay ----


def _merged(overlay: dict[str, Any]) -> Any:
    return merge(catalogue.seed().document, overlay)


def test_an_overlay_changes_only_the_fields_it_names() -> None:
    doc = _merged({"providers": {"openai": {"models": {"gpt-6.1-sol": {"prices": [{"in": 3.0, "out": 12.0}]}}}}})
    sol = doc.providers["openai"].models["gpt-6.1-sol"]
    assert sol.prices[0].pair == (3.0, 12.0)
    assert sol.chat_only is True, "overriding a price dropped the model's other fields"
    assert list(doc.providers["openai"].models) == ["gpt-6.1-sol", "gpt-6-luna"], "the seed's order moved"
    assert doc.providers["openai"].api_root == "https://api.openai.com/v1"


def test_factors_merge_key_by_key_and_a_new_model_comes_last() -> None:
    doc = _merged(
        {"providers": {"xai": {"factors": {"timeout": 3.0}, "models": {"grok-5": {"prices": [{"in": 1, "out": 2}]}}}}}
    )
    assert (doc.providers["xai"].factors.turns, doc.providers["xai"].factors.timeout) == (2.0, 3.0)
    assert list(doc.providers["xai"].models) == ["grok-4.5", "grok-4.7", "grok-5"]


def test_free_and_prices_replace_each_other() -> None:
    doc = _merged({"providers": {"openai": {"models": {"gpt-6-luna": {"free": True}}}}})
    luna = doc.providers["openai"].models["gpt-6-luna"]
    assert (luna.free, luna.prices, luna.request_extras) == (True, [], {"reasoning_effort": "none"})
    doc = merge(doc, {"providers": {"openai": {"models": {"gpt-6-luna": {"prices": [{"in": 1.0, "out": 1.0}]}}}}})
    assert doc.providers["openai"].models["gpt-6-luna"].free is False


def test_null_takes_out_a_default_price() -> None:
    doc = _merged({"providers": {"custom": {"default_price": None}}})
    assert doc.providers["custom"].default_price is None
    assert Catalogue.build(doc).default_rates("custom/qwen3") is None
    assert catalogue.seed().default_rates("custom/qwen3") == (0.0, 0.0)


def test_no_overlay_is_the_seed() -> None:
    assert merge(catalogue.seed().document, None) is catalogue.seed().document


# ---- what is refused ----


def _refused(overlay: Any) -> list[tuple[str, str]]:
    with pytest.raises(CatalogueInvalid) as caught:
        _merged(overlay)
    return [(".".join(str(part) for part in p.loc), p.msg) for p in caught.value.problems]


@pytest.mark.parametrize(
    ("overlay", "where", "says"),
    [
        ({"providers": {"mistral": {"enabled": True}}}, "providers.mistral", "not a provider embry0 ships"),
        (
            {"providers": {"openai": {"api_root": "https://proxy.example"}}},
            "providers.openai.api_root",
            "set by embry0",
        ),
        ({"providers": {"openai": {"runtime": "sdk"}}}, "providers.openai.runtime", "set by embry0"),
        ({"providers": {"groq": {"model_prefixes": ["g"]}}}, "providers.groq.model_prefixes", "set by embry0"),
        ({"providers": {"openai": {"enabled": None}}}, "providers.openai.enabled", "null takes out"),
        ({"providers": {"openai": {"enabeld": True}}}, "providers.openai.enabeld", "Extra inputs"),
        ({"providers": {"xai": {"enabled": False}}}, "", "an sdk provider is always on"),
        ({"providers": {"openai": {"models": {"gpt-new": {}}}}}, "providers.openai.models.gpt-new", "needs its prices"),
        (
            {"providers": {"openai": {"models": {"gpt-new": {"free": True, "prices": [{"in": 1, "out": 1}]}}}}},
            "providers.openai.models.gpt-new",
            "free or has prices",
        ),
        ({"providers": {"openai": {"models": {"claude-x": {"free": True}}}}}, "providers.openai", "do not start with"),
        (
            {"providers": {"openai": {"models": {"gpt-6-luna": {"prices": [{"in": -1, "out": 1}]}}}}},
            "providers.openai.models.gpt-6-luna.prices.0.in",
            "greater than or equal to 0",
        ),
        (
            {"providers": {"openai": {"models": {"gpt-6-luna": {"prices": [{"in": float("inf"), "out": 1}]}}}}},
            "providers.openai.models.gpt-6-luna.prices.0.in",
            "finite",
        ),
        (
            {"providers": {"openai": {"models": {"gpt-6-luna": {"prices": [{"in": "5", "out": 1}]}}}}},
            "providers.openai.models.gpt-6-luna.prices.0.in",
            "valid number",
        ),
        ({"providers": {"openai": {"factors": {"turns": True}}}}, "providers.openai.factors.turns", "valid number"),
        ({"providers": {"openai": {"factors": {"turns": 0}}}}, "providers.openai.factors.turns", "greater than 0"),
        (
            {
                "providers": {
                    "openai": {"models": {"gpt-6-luna": {"prices": [{"in": 1, "out": 1}, {"in": 2, "out": 2}]}}}
                }
            },
            "providers.openai.models.gpt-6-luna",
            "only the first price may have no from date",
        ),
        (
            {
                "providers": {
                    "openai": {
                        "models": {
                            "gpt-6-luna": {
                                "prices": [
                                    {"from": date(2027, 1, 1), "in": 1, "out": 1},
                                    {"from": date(2026, 1, 1), "in": 2, "out": 2},
                                ]
                            }
                        }
                    }
                }
            },
            "providers.openai.models.gpt-6-luna",
            "in order, each day once",
        ),
        (
            {"providers": {"openai": {"models": {"gpt-gone": None}}}},
            "providers.openai.models.gpt-gone",
            "no such model",
        ),
    ],
)
def test_an_overlay_the_catalogue_cannot_take_is_refused_naming_where(overlay: Any, where: str, says: str) -> None:
    problems = _refused(overlay)
    assert any(loc == where and says in msg for loc, msg in problems), f"expected {where!r}: {says!r}; got {problems}"


def test_every_problem_is_named_at_once_and_no_value_is_repeated() -> None:
    secret = "sk-not-a-price-0123456789"
    problems = _refused(
        {
            "providers": {
                "openai": {"models": {"gpt-6-luna": {"prices": [{"in": secret, "out": 1}]}}, "factors": {"turns": 99}},
                "groq": {"enabled": "yes"},
            }
        }
    )
    assert len(problems) == 3, problems
    assert secret not in str(problems) and "99" not in str(problems), "a refusal repeats the value it refuses"


def test_the_seed_is_a_whole_catalogue_and_the_anthropic_provider_is_required() -> None:
    document = catalogue.seed().document.model_dump(by_alias=True)
    del document["providers"]["anthropic"]
    with pytest.raises(CatalogueInvalid, match="needs the 'anthropic' provider"):
        parse(document)


def test_overlapping_prefixes_are_refused() -> None:
    document = catalogue.seed().document.model_dump(by_alias=True)
    document["providers"]["openai"]["model_prefixes"] = ["gpt-", "groq/openai/"]
    with pytest.raises(CatalogueInvalid, match="overlap"):
        parse(document)


def test_every_shipped_provider_has_its_variables_in_the_code() -> None:
    """Where a key is read from is code (catalogue.ENVIRONMENT), never the catalogue: every shipped provider has one."""
    assert set(catalogue.seed().document.providers) == set(catalogue.ENVIRONMENT)
    for provider in catalogue.seed().providers:
        assert dataclasses.asdict(provider)["api_key_env"] == catalogue.ENVIRONMENT[provider.name].api_key


def test_the_sandbox_image_carries_the_seed() -> None:
    """The sandbox images read the seed beside the module: the directory they copy holds it."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    assert catalogue.SEED_FILE.parent == root / "embry0" / "agents"
    for dockerfile in ("infra/Dockerfile.sandbox",):
        text = (root / dockerfile).read_text(encoding="utf-8")
        assert "COPY embry0/agents/ /app/embry0/agents/" in text, f"{dockerfile} no longer copies embry0/agents/"
