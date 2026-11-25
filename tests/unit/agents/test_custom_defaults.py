"""The custom provider's defaults (#173, spec 2026-11-23 "Plan" item 5): a custom model runs priced or free, never at
a $0 price nobody set; turn and time budgets are not doubled; and a model says, in its catalogue entry, whether a call
may hold it to a JSON schema (``structured_output``) and what else its server needs (``request_extras``)."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.agents import catalogue
from embry0.agents.providers import offered_problem
from tests.fixtures.provider_catalogue import enable_providers


def _providers() -> Any:
    import embry0.agents.providers as providers

    return providers


def test_the_seed_gives_the_custom_provider_no_price_and_no_doubled_budgets() -> None:
    custom = catalogue.seed().named("custom")
    assert custom is not None
    assert custom.default_pricing_usd_per_mtok is None, "a custom model is priced by the deployment, or refused"
    assert (custom.turn_budget_factor, custom.timeout_factor) == (1.0, 1.0), "the budgets are Claude-sized, not 2x"


def test_a_custom_model_with_neither_a_price_nor_free_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    installed = enable_providers(monkeypatch, "custom")
    assert offered_problem("custom/qwen", installed) == "the catalogue has no price for it"


@pytest.mark.parametrize(
    "entry",
    [{"free": True}, {"prices": [{"in": 0.5, "out": 1.5}]}],
    ids=["free", "priced"],
)
def test_a_custom_model_the_deployment_lists_priced_or_free_runs(
    monkeypatch: pytest.MonkeyPatch, entry: dict[str, Any]
) -> None:
    installed = enable_providers(monkeypatch, "custom", custom={"models": {"custom/qwen": entry}})
    assert offered_problem("custom/qwen", installed) is None
    assert offered_problem("custom/other", installed) == "the catalogue has no price for it", "only what it lists"


def test_a_model_says_whether_a_call_may_hold_it_to_a_schema_and_what_else_it_needs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    providers = _providers()
    if not hasattr(providers, "structured_output") or not hasattr(providers, "request_extras"):
        pytest.fail("the catalogue must say per model whether it takes a JSON schema, and its request extras")
    extras = {"chat_template_kwargs": {"enable_thinking": False}}
    installed = enable_providers(
        monkeypatch,
        "custom",
        custom={
            "models": {
                "custom/qwen": {"free": True, "structured_output": "json_schema", "request_extras": extras},
                "custom/plain": {"free": True},
            }
        },
    )
    assert providers.structured_output("custom/qwen", installed) is True
    assert providers.structured_output("custom/plain", installed) is False
    assert providers.request_extras("custom/qwen", installed) == extras
    assert providers.request_extras("custom/plain", installed) == {}


@pytest.mark.parametrize("value", ["yes", True, "json_object"])
def test_structured_output_takes_json_schema_or_nothing(value: Any) -> None:
    overlay = {"providers": {"custom": {"models": {"custom/qwen": {"free": True, "structured_output": value}}}}}
    with pytest.raises(catalogue.CatalogueInvalid):
        catalogue.merge(catalogue.seed().document, overlay)


def test_an_overlay_takes_structured_output_back_with_null() -> None:
    base = catalogue.merge(
        catalogue.seed().document,
        {"providers": {"custom": {"models": {"custom/qwen": {"free": True, "structured_output": "json_schema"}}}}},
    )
    cleared = catalogue.merge(base, {"providers": {"custom": {"models": {"custom/qwen": {"structured_output": None}}}}})
    assert cleared.providers["custom"].models["custom/qwen"].structured_output is None
