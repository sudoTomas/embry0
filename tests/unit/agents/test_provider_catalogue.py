"""The provider catalogue (#173, ADR 0031): its schema, the seed embry0 ships, an overlay merged field by field,
and ``embry0.agents.providers`` answering from the catalogue installed in the process."""

from __future__ import annotations

import dataclasses
import json
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
    assert [p.name for p in providers.routed_providers()] == ["xai", "openai"], "an enabled provider is not routed"
    assert [p.name for p in providers.catalog_providers()] == ["anthropic", "xai", "openai"]
    assert providers.model_rates("gpt-6-luna") == (0.2, 0.8), "the installed price is not the one read"
    assert providers.model_rates("gpt-7") == (0.0, 0.0), "a free model is not priced at $0"
    assert "gpt-7" in providers.known_models()
    openai = providers.provider_named("openai")
    assert openai is not None and (openai.turn_budget_factor, openai.timeout_factor) == (3.0, 1.0)
    assert providers.model_rates("gemini-4-argon") is None, "a model taken out is still priced"
    catalogue.install(None)
    assert providers.routed_providers() == providers.PROVIDERS, "uninstalling does not return to the seed"
    assert providers.model_rates("gpt-6-luna") == (0.1, 0.5)


def test_the_enabling_variable_no_longer_turns_a_provider_on(installed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _install({"providers": {"groq": {"enabled": True}}})
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "deepseek")
    routed = [p.name for p in providers.routed_providers()]
    assert routed == ["xai", "groq"], "the variable turned a provider on beside the catalogue's flag"


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
        ({"providers": {"xai": {"enabled": False}}}, "providers.xai.enabled", "an sdk provider is always on"),
        ({"providers": {"openai": {"models": {"gpt-new": {}}}}}, "providers.openai.models.gpt-new", "needs its prices"),
        (
            {"providers": {"openai": {"models": {"gpt-new": {"free": True, "prices": [{"in": 1, "out": 1}]}}}}},
            "providers.openai.models.gpt-new",
            "free or has prices",
        ),
        (
            {"providers": {"openai": {"models": {"claude-x": {"free": True}}}}},
            "providers.openai.models.claude-x",
            "does not start with",
        ),
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
            "providers.openai.models.gpt-6-luna.prices",
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
            "providers.openai.models.gpt-6-luna.prices",
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


#: Fields a model's request_extras may set, written out here, not read from the code: adding one to the allowlist
#: (catalogue.REQUEST_EXTRAS) means adding it here too.
_ALLOWED_EXTRAS = {
    "chat_template_kwargs": {"enable_thinking": False},
    "max_completion_tokens": 512,
    "max_tokens": 512,
    "reasoning_effort": "none",
    "response_format": {"type": "json_object"},
    "temperature": 0.2,
    "top_p": 0.9,
}
#: Fields it may not: the executor's own, and ones that change which model answers or what it costs.
_REFUSED_EXTRAS = [
    "model",
    "messages",
    "tools",
    "tool_choice",
    "functions",
    "function_call",
    "stream",
    "stream_options",
    "n",
    "models",
    "route",
    "service_tier",
    "modalities",
    "audio",
    "prediction",
    "web_search_options",
    "plugins",
    "provider",
]


def test_the_allowlist_is_the_one_written_here() -> None:
    assert catalogue.REQUEST_EXTRAS == set(_ALLOWED_EXTRAS), "the allowlist moved without its test"


@pytest.mark.parametrize("field", sorted(_ALLOWED_EXTRAS))
def test_request_extras_takes_a_field_that_changes_neither_model_nor_price(field: str) -> None:
    doc = _merged(
        {"providers": {"openai": {"models": {"gpt-6-luna": {"request_extras": {field: _ALLOWED_EXTRAS[field]}}}}}}
    )
    assert doc.providers["openai"].models["gpt-6-luna"].request_extras == {field: _ALLOWED_EXTRAS[field]}


@pytest.mark.parametrize("field", _REFUSED_EXTRAS)
def test_request_extras_refuses_any_other_field_naming_where(field: str) -> None:
    problems = _refused(
        {"providers": {"openrouter": {"models": {"openrouter/openai/gpt-6.1-sol": {"request_extras": {field: "x"}}}}}}
    )
    where = f"providers.openrouter.models.openrouter/openai/gpt-6.1-sol.request_extras.{field}"
    assert [loc for loc, _ in problems] == [where], f"{field} was not refused where it is: {problems}"


def test_a_routing_list_is_refused_with_its_alias() -> None:
    problems = _refused(
        {
            "providers": {
                "openrouter": {
                    "models": {
                        "openrouter/openai/gpt-6.1-sol": {
                            "request_extras": {"models": ["openrouter/anthropic/claude-opus-x"], "route": "fallback"}
                        }
                    }
                }
            }
        }
    )
    assert sorted(loc.rsplit(".", 1)[1] for loc, _ in problems) == ["models", "route"], problems


def test_null_models_is_refused_not_a_crash() -> None:
    problems = _refused({"providers": {"openai": {"models": None}}})
    assert problems == [("providers.openai.models", "null takes out a model or a default price")], problems


def test_a_field_only_embry0_sets_is_named_beside_a_wrong_shape() -> None:
    problems = _refused({"providers": {"openai": {"api_root": "https://proxy.example", "enabled": "yes"}}})
    assert sorted(loc for loc, _ in problems) == ["providers.openai.api_root", "providers.openai.enabled"], problems


def test_every_problem_of_an_overlay_and_of_the_catalogue_it_makes_is_named() -> None:
    problems = _refused(
        {
            "providers": {
                "mistral": {"enabled": True},
                "openai": {
                    "api_root": "https://proxy.example",
                    "models": {"gpt-new": {}, "gpt-6-luna": {"request_extras": {"model": "gpt-6.1-sol"}}},
                },
                "xai": {"enabled": False},
            }
        }
    )
    assert sorted(loc for loc, _ in problems) == [
        "providers.mistral",
        "providers.openai.api_root",
        "providers.openai.models.gpt-6-luna.request_extras.model",
        "providers.openai.models.gpt-new",
        "providers.xai.enabled",
    ], problems


def test_every_problem_across_a_document_s_fields_is_named() -> None:
    document = catalogue.seed().document.model_dump(by_alias=True)
    document["providers"]["openai"]["models"]["gpt-6-luna"]["prices"] = []
    document["providers"]["groq"]["models"]["groq/openai/gpt-oss-20b"]["free"] = True
    document["providers"]["xai"]["enabled"] = False
    document["providers"]["openai"]["model_prefixes"] = ["gpt-", "groq/openai/"]
    with pytest.raises(CatalogueInvalid) as caught:
        parse(document)
    assert sorted(".".join(map(str, p.loc)) for p in caught.value.problems) == [
        "providers.groq.models.groq/openai/gpt-oss-20b",
        "providers.openai.model_prefixes",
        "providers.openai.models.gpt-6-luna",
        "providers.xai.enabled",
    ], caught.value.problems


# ---- bounds on what a write may hold ----


def _extra(value: Any) -> dict[str, Any]:
    return {"providers": {"openai": {"models": {"gpt-6-luna": {"request_extras": {"response_format": value}}}}}}


_EXTRA_AT = "providers.openai.models.gpt-6-luna.request_extras.response_format"


def _nested(depth: int) -> Any:
    value: Any = "leaf"
    for _ in range(depth):
        value = {"a": value}
    return value


def test_a_request_extras_value_is_bounded_in_size() -> None:
    fits = {"type": "text", "pad": "x" * (catalogue.MAX_EXTRA_BYTES - len('{"type":"text","pad":""}'))}
    assert len(json.dumps(fits, separators=(",", ":"))) == catalogue.MAX_EXTRA_BYTES
    _merged(_extra(fits))
    fits["pad"] += "x"
    problems = _refused(_extra(fits))
    assert problems == [(_EXTRA_AT, f"larger than {catalogue.MAX_EXTRA_BYTES} bytes as JSON")]


def test_a_request_extras_value_is_bounded_in_depth() -> None:
    _merged(_extra(_nested(catalogue.MAX_EXTRA_DEPTH)))
    problems = _refused(_extra(_nested(catalogue.MAX_EXTRA_DEPTH + 1)))
    assert problems == [(_EXTRA_AT, f"nests deeper than {catalogue.MAX_EXTRA_DEPTH} levels")]


def test_a_problem_deep_in_a_document_is_named_where_it_starts() -> None:
    """A thousand-deep value does not make a thousand-part location."""
    with pytest.raises(CatalogueInvalid) as caught:
        _merged({"providers": {"openai": {"models": {"gpt-6-luna": {"request_extras": _nested(1000)}}}}})
    locs = [p.loc for p in caught.value.problems]
    assert locs and all(len(loc) <= catalogue.MAX_LOC_PARTS for loc in locs), [len(loc) for loc in locs]
    assert any(loc[-1] == catalogue.LOC_CUT for loc in locs), "a cut location does not say it was cut"
    assert locs[0][:5] == ("providers", "openai", "models", "gpt-6-luna", "request_extras")


def test_only_a_provider_with_a_live_list_takes_refresh_hours() -> None:
    assert _merged({"providers": {"openrouter": {"refresh_hours": 6}}}).providers["openrouter"].refresh_hours == 6
    assert _merged({"providers": {"openrouter": {"refresh_hours": 0}}}).providers["openrouter"].refresh_hours == 0
    assert [loc for loc, _ in _refused({"providers": {"openai": {"refresh_hours": 6}}})] == [
        "providers.openai.refresh_hours"
    ]
    for hours in (-1, 721, float("inf")):
        assert [loc for loc, _ in _refused({"providers": {"openrouter": {"refresh_hours": hours}}})] == [
            "providers.openrouter.refresh_hours"
        ], hours


# ---- a stored overlay and a seed that moved since (the design's "Seed drift") ----


def _later_seed(*, without_models: dict[str, list[str]] | None = None, without: list[str] | None = None) -> Any:
    """The seed of a later release: these models and providers taken out."""
    data = catalogue.seed().document.model_dump(mode="json", by_alias=True)
    for provider, models in (without_models or {}).items():
        for model in models:
            del data["providers"][provider]["models"][model]
    for provider in without or []:
        del data["providers"][provider]
    return catalogue.parse(data)


def _stored(base: Any, overlay: Any, shipped: Any = None) -> tuple[Any, list[tuple[str, str]]]:
    read = catalogue.merge_stored(base, overlay, shipped)
    return read.document, [(".".join(str(part) for part in p.loc), p.type) for p in read.refused]


def _shipped(*models: tuple[str, str]) -> dict[str, dict[str, Any]]:
    """What a write recorded of these shipped models (catalogue.shipped_entries), against today's seed."""
    overlay = {"providers": {provider: {"models": {model: {}}} for provider, model in models}}
    for provider, model in models:
        overlay["providers"][provider]["models"][model] = {}
    return catalogue.shipped_entries(catalogue.seed().document, overlay)


def test_a_model_the_overlay_took_out_and_the_seed_dropped_too_changes_nothing() -> None:
    """The review's case: a release drops gpt-6.1-sol, which the overlay had nulled; nothing else is lost."""
    overlay = {
        "providers": {
            "openai": {"enabled": True, "models": {"gpt-6.1-sol": None}},
            "custom": {"enabled": True, "default_price": {"in": 1.0, "out": 2.0}},
        }
    }
    base = _later_seed(without_models={"openai": ["gpt-6.1-sol"]})
    with pytest.raises(CatalogueInvalid):
        catalogue.merge(base, overlay)  # a write of it is refused: the model is not there to take out
    document, refused = _stored(base, overlay)
    assert refused == [], "what the null asked for holds: no problem"
    assert document.providers["openai"].enabled and document.providers["custom"].enabled
    assert document.providers["custom"].default_price == catalogue.Rates.model_validate({"in": 1.0, "out": 2.0})


def test_a_provider_the_seed_dropped_is_left_out_and_the_rest_stays() -> None:
    overlay = {"providers": {"groq": {"enabled": True}, "deepseek": {"enabled": True}}}
    document, refused = _stored(_later_seed(without=["groq"]), overlay)
    assert refused == [("providers.groq", "not_shipped")]
    assert document.providers["deepseek"].enabled, "a provider the deployment turned on was turned off"


def test_a_model_the_seed_dropped_is_kept_as_the_deployment_s_own_only_with_its_own_prices() -> None:
    """The overlay re-priced gpt-6.1-sol (chat-only as shipped), made deepseek-v4-pro free, and only touched
    gpt-6-luna's flag; a later release drops all three."""
    shipped = _shipped(("openai", "gpt-6.1-sol"), ("openai", "gpt-6-luna"), ("deepseek", "deepseek-v4-pro"))
    base = _later_seed(without_models={"openai": ["gpt-6.1-sol", "gpt-6-luna"], "deepseek": ["deepseek-v4-pro"]})
    overlay = {
        "providers": {
            "openai": {
                "enabled": True,
                "models": {"gpt-6.1-sol": {"prices": [{"in": 3.0, "out": 12.0}]}, "gpt-6-luna": {"chat_only": True}},
            },
            "deepseek": {"enabled": True, "models": {"deepseek-v4-pro": {"free": True}}},
        }
    }
    read = catalogue.merge_stored(base, overlay, shipped)
    kept = read.document.providers["openai"].models["gpt-6.1-sol"]
    assert (kept.prices[0].pair, kept.chat_only) == ((3.0, 12.0), True), "it lost what it was shipped with"
    assert [p.loc for p in read.notes] == [("providers", "openai", "models", "gpt-6.1-sol")], "kept, unnamed"
    refused = sorted((".".join(map(str, p.loc)), p.type) for p in read.refused)
    assert refused == [
        ("providers.deepseek.models.deepseek-v4-pro", "no_longer_shipped"),
        ("providers.deepseek.models.deepseek-v4-pro", "withheld"),
        ("providers.openai.models.gpt-6-luna", "no_longer_shipped"),
    ], "only a model with prices of its own is kept"
    assert read.withheld == frozenset({"deepseek-v4-pro"}), "free said a price: it is withheld, not dropped"
    assert read.document.providers["openai"].enabled and read.document.providers["deepseek"].enabled


def test_a_model_the_deployment_added_is_read_as_written() -> None:
    """No record: it was never shipped, so it is the deployment's own from the start, chat_only as it says."""
    overlay = {
        "providers": {"deepseek": {"enabled": True, "models": {"deepseek-next": {"prices": [{"in": 1.0, "out": 3.0}]}}}}
    }
    read = catalogue.merge_stored(catalogue.seed().document, overlay, {})
    assert (read.refused, read.notes, read.withheld) == ([], [], frozenset())
    assert read.document.providers["deepseek"].models["deepseek-next"].chat_only is False


def test_a_price_left_out_leaves_what_it_priced_unpriced() -> None:
    """A model's dated prices and the custom provider's default the release refuses: never the seed's in their place."""
    overlay = {
        "providers": {
            "deepseek": {
                "enabled": True,
                "models": {
                    "deepseek-flash": {
                        "prices": [{"in": 9.0, "out": 9.0, "from": date(2027, 1, 1)}, {"in": 1.0, "out": 1.0}]
                    }
                },
            },
            "custom": {"enabled": True, "default_price": {"in": 3.0, "out": -15.0}},
        }
    }
    read = catalogue.merge_stored(catalogue.seed().document, overlay)
    built = catalogue.Catalogue.build(read.document, withheld=read.withheld)
    assert ("providers", "deepseek", "models", "deepseek-flash") in [
        p.loc for p in read.refused if p.type == "withheld"
    ]
    assert ("providers", "custom", "default_price") in [p.loc for p in read.refused if p.type == "withheld"]
    assert providers.model_rates("deepseek-flash", built) is None, "the seed's price stood in for the deployment's"
    assert providers.model_rates("custom/llama3", built) is None, "custom ran at $0 in place of the price set"
    assert providers.unpriced_models(["deepseek-flash", "custom/llama3"], built) == ["custom/llama3", "deepseek-flash"]
    assert built.enabled("deepseek") and built.enabled("custom"), "the provider stays on for its other models"
    assert providers.model_rates("deepseek-v4-pro", built) == (1.32, 3.96)


def test_an_unknown_top_level_key_is_left_out_alone() -> None:
    document, refused = _stored(
        catalogue.seed().document, {"pricing_policy": "strict", "providers": {"deepseek": {"enabled": True}}}
    )
    assert refused == [("pricing_policy", "extra_forbidden")]
    assert document.providers["deepseek"].enabled


def test_a_dated_price_survives_its_json_form() -> None:
    """Stored as JSON (catalogue.stored_form), a from day is ISO text; read back, it is a day again."""
    overlay = {
        "providers": {
            "deepseek": {
                "models": {
                    "deepseek-flash": {
                        "prices": [{"in": 1.0, "out": 1.0}, {"from": date(2027, 1, 1), "in": 9.0, "out": 9.0}]
                    }
                }
            }
        }
    }
    stored = json.loads(json.dumps(catalogue.stored_form(overlay)))
    assert stored["providers"]["deepseek"]["models"]["deepseek-flash"]["prices"][1]["from"] == "2027-01-01"
    read = catalogue.merge_stored(catalogue.seed().document, stored)
    assert read.refused == [], "a dated price read back from JSON was refused"
    assert read.document.providers["deepseek"].models["deepseek-flash"].prices[1].from_ == date(2027, 1, 1)


def test_a_field_or_a_value_the_release_refuses_is_left_out_alone() -> None:
    overlay = {
        "providers": {
            "openai": {"enabled": True, "base_url": "https://elsewhere.test", "enabeld": True},
            "deepseek": {
                "enabled": True,
                "factors": {"turns": 99},
                "models": {"deepseek-flash": {"prices": [{"in": -1, "out": 1}]}},
            },
        }
    }
    read = catalogue.merge_stored(catalogue.seed().document, overlay)
    document = read.document
    assert sorted(".".join(map(str, p.loc)) for p in read.refused) == [
        "providers.deepseek.factors.turns",
        "providers.deepseek.models.deepseek-flash",
        "providers.deepseek.models.deepseek-flash.prices.0.in",
        "providers.openai.base_url",
        "providers.openai.enabeld",
    ]
    assert document.providers["openai"].enabled and document.providers["deepseek"].enabled
    assert document.providers["openai"].base_url == catalogue.seed().document.providers["openai"].base_url
    assert document.providers["deepseek"].factors.turns == 1.0
    assert read.withheld == frozenset({"deepseek-flash"}), "a price left out: the model is unpriced, not as shipped"


@pytest.mark.parametrize("overlay", [["providers"], {"version": 2, "providers": {"deepseek": {"enabled": True}}}])
def test_an_overlay_that_is_none_at_all_leaves_the_seed_and_says_so(overlay: Any) -> None:
    document, refused = _stored(catalogue.seed().document, overlay)
    assert document == catalogue.seed().document
    assert refused and refused[-1][1] in {"not_a_mapping", "not_applied"}, refused


def test_an_overlay_that_applies_whole_leaves_nothing_out() -> None:
    overlay = {"providers": {"deepseek": {"enabled": True}}}
    document, refused = _stored(catalogue.seed().document, overlay)
    assert (refused, document) == ([], catalogue.merge(catalogue.seed().document, overlay))


def test_a_withheld_model_is_unpriced_even_on_openrouter_s_live_list() -> None:
    from embry0.agents import openrouter_catalog as oc

    model = "openrouter/openai/gpt-6.1-sol"
    oc.install(oc.Catalog(models={model: oc.CatalogModel(model, "x", 1.0, 2.0)}, fetched_at=datetime.now(UTC)))
    try:
        withheld = catalogue.Catalogue.build(catalogue.seed().document, withheld=frozenset({model}))
        assert providers.model_rates(model, withheld) is None, "the live list priced a model whose price is withheld"
        assert providers.model_rates(model, catalogue.seed()) == (1.0, 2.0)
    finally:
        oc.install(None)


def test_a_withheld_model_has_no_rates_of_its_own_nor_its_provider_s_default() -> None:
    """custom/qwen's price is refused while the custom provider keeps its default: neither prices it."""
    overlay = {
        "providers": {"custom": {"enabled": True, "models": {"custom/qwen": {"prices": [{"in": -1.0, "out": 1.0}]}}}}
    }
    read = catalogue.merge_stored(catalogue.seed().document, overlay)
    built = catalogue.Catalogue.build(read.document, withheld=read.withheld)
    assert read.withheld == frozenset({"custom/qwen"})
    assert built.rates("custom/qwen") is None
    assert built.default_rates("custom/qwen") is None, "the provider's default priced a model whose price is withheld"
    assert built.default_rates("custom/other") == (0.0, 0.0), "the provider's other models keep its default"
    shipped_model = catalogue.Catalogue.build(catalogue.seed().document, withheld=frozenset({"deepseek-flash"}))
    assert shipped_model.rates("deepseek-flash") is None, "the seed's entry priced a model whose price is withheld"
    assert "deepseek-flash" not in shipped_model.named("deepseek").pricing_usd_per_mtok


def test_a_write_records_every_dropped_model_it_knew_of_named_or_not() -> None:
    """The record of a model the release no longer ships outlives the overlay's naming it; one it still ships is
    this release's entry, and only for a model the overlay names."""
    previous = _shipped(("openai", "gpt-6.1-sol"), ("openai", "gpt-6-luna"))
    base = _later_seed(without_models={"openai": ["gpt-6.1-sol"]})
    written = catalogue.shipped_entries(base, {"providers": {"deepseek": {"models": {"deepseek-flash": {}}}}}, previous)
    assert written["openai"] == {"gpt-6.1-sol": previous["openai"]["gpt-6.1-sol"]}, "a dropped model's record was lost"
    assert written["deepseek"]["deepseek-flash"]["prices"][0] == {"in": 0.3, "out": 1.2, "from": None}
    later = catalogue.records(previous, {"openai": {"gpt-6.1-sol": {"chat_only": False}}})
    assert later["openai"]["gpt-6.1-sol"] == {"chat_only": False} and "gpt-6-luna" in later["openai"]
