"""The OpenAI-compatible provider family in the catalog: routing, enablement, pricing."""

from __future__ import annotations

import pytest

from embry0.agents import providers as catalog
from embry0.agents.providers import provider_for_model

# (provider, a model it serves, the id its API knows that model by)
_ROUTES = [
    ("openai", "gpt-6-luna", "gpt-6-luna"),
    ("openrouter", "openrouter/openai/gpt-6.1-sol", "openai/gpt-6.1-sol"),
    ("google", "gemini-3.8-flash", "gemini-3.8-flash"),
    ("groq", "groq/openai/gpt-oss-120b", "openai/gpt-oss-120b"),
    ("deepseek", "deepseek-flash", "deepseek-flash"),
    ("custom", "custom/llama3.3:70b", "llama3.3:70b"),
]
_FAMILY = [name for name, _, _ in _ROUTES]


@pytest.fixture
def enable(monkeypatch):
    def _enable(*names: str) -> None:
        monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", ",".join(names))

    return _enable


@pytest.mark.parametrize(("name", "model", "upstream"), _ROUTES)
def test_each_provider_serves_its_models_once_enabled(enable, name, model, upstream) -> None:
    enable(name)
    provider = provider_for_model(model)
    assert provider is not None and provider.name == name, (
        f"{model!r} must route to {name} once {name} is enabled, got {provider and provider.name}"
    )
    assert catalog.upstream_model(provider, model) == upstream, "the routing prefix must not reach the provider"
    assert provider.runtime == "chat_completions"


@pytest.mark.parametrize(("name", "model", "_upstream"), _ROUTES)
def test_off_by_default_every_model_routes_as_before(monkeypatch, name, model, _upstream) -> None:
    monkeypatch.delenv("OPENAI_COMPATIBLE_PROVIDERS", raising=False)
    assert provider_for_model(model) is None, f"{model!r} routed to a provider nobody enabled"
    assert catalog.unpriced_models([model]) == [], "an id that routes nowhere is not price-checked, as before"
    assert name not in {p.name for p in catalog.catalog_providers()}, f"{name} listed while off"
    assert model not in catalog.known_models()


def test_enabling_one_provider_leaves_the_others_off(enable) -> None:
    enable("deepseek")
    assert [p.name for p in catalog.catalog_providers()] == ["anthropic", "xai", "deepseek"]
    assert provider_for_model("gpt-6-luna") is None, "openai is not enabled"
    assert "deepseek-flash" in catalog.known_models()


def test_unknown_names_in_the_setting_enable_nothing(enable) -> None:
    enable("openia", "", " DeepSeek ")
    assert [p.name for p in catalog.enabled_openai_compatible()] == ["deepseek"], "names are trimmed, case-folded"


@pytest.mark.parametrize("setting", ["", "openai,openrouter,google,groq,deepseek,custom"])
def test_xai_routing_does_not_depend_on_the_setting(monkeypatch, setting) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", setting)
    assert provider_for_model("grok-4.7") is catalog.XAI
    assert catalog.XAI.runtime == "sdk", "grok keeps the Agent SDK runtime"
    assert provider_for_model("claude-sonnet-5") is None


def test_xai_is_a_member_of_the_family_by_its_openai_compatible_root() -> None:
    assert catalog.XAI.openai_base_url == "https://api.x.ai/v1"
    assert catalog.XAI.base_url == "https://api.x.ai", "the Agent SDK overlay keeps the Anthropic-compatible root"


def test_no_model_id_can_match_two_providers() -> None:
    prefixes = [(p.name, prefix) for p in catalog.ALL_PROVIDERS for prefix in p.model_prefixes]
    clashes = [(a, b) for a in prefixes for b in prefixes if a[0] != b[0] and b[1].startswith(a[1])]
    assert clashes == [], f"overlapping prefixes route one id two ways: {clashes}"


def test_every_listed_model_of_every_provider_is_priced() -> None:
    unpriced = [m for p in catalog.ALL_PROVIDERS for m in p.models if m not in p.pricing_usd_per_mtok]
    assert unpriced == [], f"listed models without a price: {unpriced}"


def test_an_unlisted_model_of_a_metered_provider_is_refused(enable) -> None:
    enable("openai", "custom")
    assert catalog.unpriced_models(["gpt-7-unlisted"]) == ["gpt-7-unlisted"]
    assert catalog.unpriced_models(["custom/qwen3:32b"]) == [], "a self-hosted model is priced at $0, not refused"
    assert catalog.price_usage("custom/qwen3:32b", input_tokens=10**6, output_tokens=10**6) == 0.0


def test_listed_prices_are_applied(enable) -> None:
    enable("groq")
    cost = catalog.price_usage("groq/openai/gpt-oss-120b", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(0.15 + 0.60)


def test_the_sandbox_prices_a_routed_model_without_the_setting(monkeypatch) -> None:
    """The setting lives in the orchestrator's environment, not the sandbox's."""
    monkeypatch.delenv("OPENAI_COMPATIBLE_PROVIDERS", raising=False)
    assert catalog.price_usage("deepseek-flash", input_tokens=1_000_000, output_tokens=0) == pytest.approx(0.30)
    assert catalog.provider_named("deepseek") is catalog.DEEPSEEK


def _resolve(model: str, tools: list[str]):
    from embry0.agents.resolver import resolve_agent_invocation

    return resolve_agent_invocation(
        agent_type="developer",
        prompt="p",
        system_context="",
        global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
        repo_prefs=None,
        job_overrides={},
        agent_definition={
            "model": model,
            "tools": tools,
            "skills": [],
            "system_prompt": "",
            "mcp_servers": {},
            "execution_mode": None,
            "auth_mode": None,
        },
        pipeline_config={},
        max_turns=40,
        timeout_seconds=600,
        credentials={"api_key": "", "oauth_token": "tok"},
    )


def test_resolver_stamps_the_provider_and_keeps_claude_budgets(enable) -> None:
    enable("openai")
    inv = _resolve("gpt-6-luna", ["Read", "Bash"])
    assert inv.provider == "openai"
    assert (inv.max_turns, inv.timeout_seconds) == (40, 600), "only a provider that declares factors scales budgets"


def test_resolver_refuses_tools_on_a_chat_only_model(enable) -> None:
    from embry0.execution.auth_provider import AuthConfigError
    from embry0.safety.error_codes import ErrorCode

    enable("openai")
    with pytest.raises(AuthConfigError) as exc:
        _resolve("gpt-6.1-sol", ["Read", "Bash"])
    assert exc.value.error_code == ErrorCode.INVALID_CONFIG
    assert "chat-only" in str(exc.value) and "gpt-6.1-sol" in str(exc.value)


def test_resolver_runs_a_toolless_agent_on_a_chat_only_model(enable) -> None:
    enable("openai")
    assert _resolve("gpt-6.1-sol", []).provider == "openai"


def test_enabling_providers_pulls_none_into_a_job_that_names_none_of_their_models(enable) -> None:
    """A provider is resolved for a job only when one of the job's models routes to it."""
    from embry0.services.credentials.providers import providers_for_job
    from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

    enable(*_FAMILY)
    assert providers_for_job(list(BUILTIN_SEED.values()), None, None) == ("anthropic",)
    assert providers_for_job(list(BUILTIN_SEED.values()), {"developer": "deepseek-flash"}, None) == (
        "anthropic",
        "deepseek",
    )
