"""LLM accounts of the OpenAI-compatible providers: the provider table, env fallback, registry metadata, import."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from embry0.services.credentials.providers import env_fallback, env_fallback_metadata, llm_providers
from embry0.services.credentials.resolver import CredentialMissingError
from tests.unit.services.credentials.test_resolve_llm import _llm, _row

_ON = {"OPENAI_COMPATIBLE_PROVIDERS": "openai,custom"}


def test_the_provider_table_lists_only_what_the_deployment_enabled() -> None:
    assert set(llm_providers({})) == {"anthropic", "xai"}, "off by default"
    table = llm_providers(_ON)
    assert set(table) == {"anthropic", "xai", "openai", "custom"}
    assert (table["openai"].host, table["openai"].kinds) == ("api.openai.com", ("llm_api_key",))
    assert table["custom"].host == "", "a custom endpoint has no host of its own"


def test_env_fallback_reads_the_providers_key_variable() -> None:
    env = {**_ON, "OPENAI_API_KEY": " sk-env "}
    assert env_fallback("openai", env, SimpleNamespace()) == ("llm_api_key", "sk-env", "OPENAI_API_KEY")
    with pytest.raises(ValueError, match="openai"):
        env_fallback("openai", {"OPENAI_API_KEY": "sk-env"}, SimpleNamespace())


def test_a_custom_env_key_counts_only_with_its_base_url() -> None:
    env = {**_ON, "CUSTOM_LLM_API_KEY": "ollama-any"}
    assert env_fallback("custom", env, SimpleNamespace()) is None
    env["CUSTOM_LLM_BASE_URL"] = "http://10.0.0.5:11434/v1/"
    assert env_fallback("custom", env, SimpleNamespace()) == ("llm_api_key", "ollama-any", "CUSTOM_LLM_API_KEY")
    assert env_fallback_metadata("custom", env) == {"base_url": "http://10.0.0.5:11434/v1"}


async def test_resolution_falls_back_to_the_env_key_with_its_host() -> None:
    env = {**_ON, "CUSTOM_LLM_API_KEY": "ollama-any", "CUSTOM_LLM_BASE_URL": "http://gpu-box.lan:8000/v1"}
    got = await _llm(env=env).resolve_llm("acme/widgets", "custom")
    assert (got.source, got.secret, got.host) == ("env", "ollama-any", "gpu-box.lan")
    assert got.metadata == {"base_url": "http://gpu-box.lan:8000/v1"}


async def test_a_registry_key_carries_its_metadata() -> None:
    row = {
        **_row("ollama", "tok-custom", provider="custom", kind="llm_api_key"),
        "host": "10.0.0.5",
        "metadata": {"base_url": "http://10.0.0.5:11434/v1"},
    }
    got = await _llm({("global", "", "llm", "custom"): [row]}, env=_ON).resolve_llm("acme/widgets", "custom")
    assert (got.source, got.secret) == ("registry", "tok-custom")
    assert got.metadata == {"base_url": "http://10.0.0.5:11434/v1"}


async def test_no_key_anywhere_is_credential_missing() -> None:
    with pytest.raises(CredentialMissingError):
        await _llm(env=_ON).resolve_llm("acme/widgets", "openai")


async def test_a_provider_that_is_not_enabled_is_never_resolved() -> None:
    with pytest.raises(ValueError, match="unknown llm provider 'openai'"):
        await _llm(env={"OPENAI_API_KEY": "sk-env"}).resolve_llm("acme/widgets", "openai")


async def test_import_env_moves_enabled_keys_into_the_registry(monkeypatch) -> None:
    from embry0.services.credentials.import_env import import_from_env
    from tests.unit.services.credentials.test_import_env import FakeRepo, FakeSecrets

    env = {
        **_ON,
        "OPENAI_API_KEY": "sk-import",
        "CUSTOM_LLM_API_KEY": "ollama-any",
        "CUSTOM_LLM_BASE_URL": "http://10.0.0.5:11434/v1",
        "GROQ_API_KEY": "gsk-not-enabled",
    }
    repo = FakeRepo()
    report = await import_from_env(
        repo,
        FakeSecrets(),
        SimpleNamespace(gitlab_url=""),
        environ=env,
        claude_credentials_file="/no/such/file",
        xai_store_path="/no/such/store",
        grok_auth_file="/no/such/auth.json",
    )
    assert "openai/api-key" in report.created and "custom/api-key" in report.created
    assert ("groq", "api-key") not in repo.rows, "a provider that is not enabled is not imported"
    custom = repo.rows[("custom", "api-key")]
    assert (custom["host"], custom["metadata"]) == ("10.0.0.5", {"base_url": "http://10.0.0.5:11434/v1"})
    assert repo.rows[("openai", "api-key")]["host"] == "api.openai.com"
