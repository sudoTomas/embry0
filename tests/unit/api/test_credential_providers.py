"""A credential's LLM provider is one of the provider catalogue's, not a list in the code (#173).

The request schema named every LLM provider in a ``Literal``, so a provider a release added to the catalogue took a
code change before its key could be stored. The schema now takes any provider name by the catalogue's rule and checks
it against the catalogue in force: enabled for a new key, and an API key only for a chat-completions provider.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from embry0.agents import catalogue
from embry0.api.schemas.credentials import CredentialCreate


def _with_provider(monkeypatch: pytest.MonkeyPatch, name: str, *, enabled: bool) -> None:
    """Install the shipped catalogue with one more chat-completions provider, as a later release would ship it: its
    entry in the seed, and the variables its key is read from in the code (addresses and key sources stay out of the
    document, ADR 0031)."""
    upper = name.upper()
    monkeypatch.setitem(
        catalogue.ENVIRONMENT,
        name,
        catalogue.Environment(api_key=f"{upper}_API_KEY", sandbox_key=f"EMBRY0_LLM_{upper}_API_KEY"),
    )
    raw = catalogue.seed().document.model_dump(mode="json", by_alias=True)
    entry = dict(raw["providers"]["groq"])
    entry.update(enabled=enabled, model_prefixes=[f"{name}/"])
    raw["providers"][name] = entry
    built = catalogue.Catalogue.build(type(catalogue.seed().document).model_validate(raw))
    monkeypatch.setattr(catalogue, "_installed", built)


def _create(**fields: Any) -> CredentialCreate:
    return CredentialCreate(**{"label": "key", "secret": "sk-test-000000000001", **fields})


def test_a_provider_the_catalogue_adds_takes_a_key_with_no_code_change(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_provider(monkeypatch, "mistral", enabled=True)
    try:
        made = _create(kind="llm_api_key", provider="mistral")
    except ValidationError as exc:
        pytest.fail(f"a provider the catalogue lists and enables must take a key: {exc}")
    assert made.provider == "mistral"


def test_a_provider_the_catalogue_lists_but_does_not_enable_is_refused_saying_where_to_turn_it_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_provider(monkeypatch, "mistral", enabled=False)
    with pytest.raises(ValidationError, match=r"'mistral' is not enabled in this deployment.*providers/catalog"):
        _create(kind="llm_api_key", provider="mistral")


@pytest.mark.parametrize(
    ("kind", "provider", "said"),
    [
        ("llm_api_key", "nosuch", "must use a provider of the provider catalogue this deployment enables"),
        ("llm_api_key", "github", "must use a provider of the provider catalogue"),
        ("forge_token", "mistral", "forge_token credentials must use provider github or gitlab"),
        ("llm_oauth", "mistral", "takes an API key only"),
        ("llm_api_key", "Mistral", "should match pattern"),
    ],
)
def test_what_the_catalogue_does_not_allow_is_refused(
    monkeypatch: pytest.MonkeyPatch, kind: str, provider: str, said: str
) -> None:
    _with_provider(monkeypatch, "mistral", enabled=True)
    with pytest.raises(ValidationError, match=said):
        _create(kind=kind, provider=provider)


def test_the_schema_names_no_llm_provider() -> None:
    """The OpenAPI schema lists no provider: a client reads the providers from the catalogue (GET
    /config/providers/catalog), which may hold one this release's code never named."""
    provider = CredentialCreate.model_json_schema()["properties"]["provider"]
    assert "enum" not in provider and "anthropic" not in str(provider), provider


@pytest.mark.parametrize(
    ("kind", "said"),
    [
        ("forge_token", "forge_token credentials must use provider github or gitlab"),
        ("tailnet_api_key", "tailnet_api_key credentials use provider tailscale"),
        ("llm_oauth", "takes an API key only"),
    ],
)
def test_a_kind_the_provider_does_not_take_is_refused_for_its_kind_not_told_to_turn_it_on(
    monkeypatch: pytest.MonkeyPatch, kind: str, said: str
) -> None:
    """Turning the provider on would not make the credential valid, so the hint would send the operator the wrong
    way."""
    _with_provider(monkeypatch, "mistral", enabled=False)
    with pytest.raises(ValidationError) as refused:
        _create(kind=kind, provider="mistral")
    assert said in str(refused.value) and "turn it on" not in str(refused.value), str(refused.value)
