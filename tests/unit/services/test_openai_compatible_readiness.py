"""Readiness and the live probe of the OpenAI-compatible providers, against a fake server."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.agents.providers import CUSTOM, OPENROUTER, XAI
from embry0.services import provider_probe as probe
from embry0.services.credentials.resolver import CredentialMissingError, ResolvedCredential
from embry0.services.provider_readiness import (
    ProviderNotReadyError,
    annotate_agent,
    ensure_model_provider_ready,
    readiness_by_provider,
)
from tests.fixtures.openai_compatible_server import FakeOpenAIServer

_KEYS = ("OPENAI_API_KEY", "CUSTOM_LLM_API_KEY", "CUSTOM_LLM_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN", "XAI_API_KEY")
_PROBE_KEY = "sk-probe"
_BAD_KEY = "sk-bad"
_REGISTRY_KEY = "sk-registry"


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "openai,custom")


@pytest.fixture
def server():
    fake = FakeOpenAIServer(keys=[_PROBE_KEY, _BAD_KEY, _REGISTRY_KEY])
    with fake.running():
        yield fake


def _state(registry: set[str] = frozenset()) -> SimpleNamespace:  # type: ignore[assignment]
    repo = MagicMock()
    repo.any_for_provider = AsyncMock(side_effect=lambda provider: provider in registry)
    return SimpleNamespace(credentials_repo=repo, xai_refresher=None)


async def test_a_provider_without_a_credential_is_not_ready() -> None:
    readiness = await readiness_by_provider(_state())
    assert (readiness["openai"].ready, readiness["openai"].credential_source) == (False, None)
    assert readiness["custom"].ready is False


async def test_an_env_key_or_a_registry_row_makes_it_ready(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    readiness = await readiness_by_provider(_state(registry={"custom"}))
    assert (readiness["openai"].ready, readiness["openai"].credential_source) == (True, "api_key")
    assert (readiness["custom"].ready, readiness["custom"].credential_source) == (True, "registry")


async def test_a_custom_env_key_without_its_base_url_is_not_ready(monkeypatch) -> None:
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "any")
    assert (await readiness_by_provider(_state()))["custom"].ready is False
    monkeypatch.setenv("CUSTOM_LLM_BASE_URL", "http://10.0.0.5:11434/v1")
    assert (await readiness_by_provider(_state()))["custom"].ready is True


async def test_a_not_ready_provider_is_never_picked() -> None:
    """The opt-in gate refuses it, and the agent views say it cannot run."""
    with pytest.raises(ProviderNotReadyError, match="'openai' is not ready"):
        await ensure_model_provider_ready("gpt-6-luna", _state())
    annotated = annotate_agent({"type": "developer", "model": "gpt-6-luna"}, await readiness_by_provider(_state()))
    assert (annotated["provider"], annotated["provider_ready"]) == ("openai", False)


async def test_while_off_the_providers_are_not_in_the_readiness_report(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_COMPATIBLE_PROVIDERS")
    assert set(await readiness_by_provider(_state())) == {"anthropic", "xai"}
    with pytest.raises(ProviderNotReadyError, match="no configured provider serves"):
        await ensure_model_provider_ready("gpt-6-luna", _state())


def test_probe_urls_hang_off_the_openai_compatible_root() -> None:
    assert probe.probe_url(XAI) == "https://api.x.ai/v1/models", "xAI's probe is the one it always was"
    assert probe.probe_url(OPENROUTER) == "https://openrouter.ai/api/v1/key", "OpenRouter's /models needs no key"
    assert probe.probe_url(CUSTOM) == "", "a custom root is the operator's"
    assert probe.probe_url(CUSTOM, "http://10.0.0.5:11434/v1/") == "http://10.0.0.5:11434/v1/models"


async def test_the_env_key_is_probed_on_the_models_list(server, monkeypatch) -> None:
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", _PROBE_KEY)
    monkeypatch.setenv("CUSTOM_LLM_BASE_URL", server.url)
    checks = await probe.probe_chat_completions_provider(CUSTOM)
    assert [(c.source, c.status) for c in checks] == [("api_key", "ok")]
    (request,) = server.requests
    assert (request["method"], request["path"]) == ("GET", "/v1/models")
    assert request["headers"]["Authorization"] == f"Bearer {_PROBE_KEY}"


async def _get_from_server(server: FakeOpenAIServer, path: str, **headers: str) -> int:
    """GET ``path`` from the fake server's port directly, as another process on the host can."""
    async with AsyncClient() as client:
        return (await client.get(server.url.removesuffix("/v1") + path, headers=headers)).status_code


async def test_a_stray_request_to_the_server_changes_no_result(server, monkeypatch) -> None:
    """Any process on the host can reach the fake server's port. In a full run
    one reached it during the env-key probe above, whose ``(request,) =
    server.requests`` then found two requests (#221). Counted, a stray could
    as well pass a test that expects a request."""
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", _PROBE_KEY)
    monkeypatch.setenv("CUSTOM_LLM_BASE_URL", server.url)
    assert await _get_from_server(server, "/") == 404, "the stray request did not reach the server"
    checks = await probe.probe_chat_completions_provider(CUSTOM)
    assert await _get_from_server(server, "/favicon.ico") == 404, "the stray request did not reach the server"

    assert [(c.source, c.status) for c in checks] == [("api_key", "ok")]
    counted = [(r["method"], r["path"]) for r in server.requests]
    assert counted == [("GET", "/v1/models")], f"only the probe's request may count, not the strays: {counted}"
    # A subset: a real stray from another process lands here too.
    strays = {(r["method"], r["path"]) for r in server.ignored}
    assert {("GET", "/"), ("GET", "/favicon.ico")} <= strays, f"the strays were not kept aside: {strays}"


async def test_a_request_carrying_a_key_counts_on_any_path(server) -> None:
    """Code that sends the key off the API root, as a client that drops the
    base URL's path does, still calls the server, and no other process has the
    key: a request carrying it counts wherever it goes; one without it, off the
    API root, does not."""
    await _get_from_server(server, "/models", Authorization=f"Bearer {_PROBE_KEY}")
    await _get_from_server(server, "/models", Authorization="Bearer someone-elses-key")

    counted = [(r["path"], r["headers"].get("Authorization")) for r in server.requests]
    assert counted == [("/models", f"Bearer {_PROBE_KEY}")], (
        f"a request carrying a key the test set must count on any path, and no other: {counted}"
    )


async def test_a_rejected_key_is_unauthorized(server, monkeypatch) -> None:
    server.get_status = 401
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", _BAD_KEY)
    monkeypatch.setenv("CUSTOM_LLM_BASE_URL", server.url)
    checks = await probe.probe_chat_completions_provider(CUSTOM)
    assert checks[0].status == probe.STATUS_UNAUTHORIZED
    assert _BAD_KEY not in checks[0].message


async def test_the_registry_credential_a_job_would_get_is_probed(server) -> None:
    resolver = MagicMock()
    resolver.resolve_llm = AsyncMock(
        return_value=ResolvedCredential(
            id="cred-1",
            kind="llm_api_key",
            provider="custom",
            host="127.0.0.1",
            label="ollama",
            secret=_REGISTRY_KEY,
            git_author_name=None,
            git_author_email=None,
            source="registry",
            trace=(),
            metadata={"base_url": server.url},
        )
    )
    checks = await probe.probe_chat_completions_provider(CUSTOM, resolver)
    assert [(c.source, c.status) for c in checks] == [("registry", "ok")]
    assert resolver.resolve_llm.await_args.args == ("", "custom"), "the global pool, as a job with no binding sees it"
    assert server.requests[0]["headers"]["Authorization"] == f"Bearer {_REGISTRY_KEY}"


async def test_no_credential_anywhere_is_not_configured() -> None:
    resolver = MagicMock()
    resolver.resolve_llm = AsyncMock(side_effect=CredentialMissingError("none"))
    checks = await probe.probe_chat_completions_provider(CUSTOM, resolver)
    assert checks[0].status == probe.STATUS_NOT_CONFIGURED
    assert "CUSTOM_LLM_API_KEY" in checks[0].message


def _app() -> Any:
    from embry0.api.app import create_app
    from embry0.config import Embry0Config

    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    provider_repo = MagicMock()
    provider_repo.get = AsyncMock(return_value={})
    app.state.provider_repo = provider_repo
    app.state.credential_resolver = None
    return app


async def test_the_page_lists_the_family_and_caches_the_last_test(server, monkeypatch) -> None:
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", _PROBE_KEY)
    monkeypatch.setenv("CUSTOM_LLM_BASE_URL", server.url)
    app = _app()
    written: list[dict[str, Any]] = []

    async def fake_audit(_db: Any, action: str, **kw: Any) -> None:
        written.append({"action": action, **kw["details"]})

    monkeypatch.setattr("embry0.api.v1.config.emit_audit", fake_audit)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        tested = await client.post(
            "/api/v1/config/providers/custom/test", headers={"X-Requested-With": "XMLHttpRequest"}
        )
        catalog = (await client.get("/api/v1/config/models")).json()
    assert tested.status_code == 200 and tested.json()["status"] == "ok", tested.text
    assert written == [{"action": "provider_connection_tested", "provider": "custom", "status": "ok"}]
    by_name = {p["name"]: p for p in catalog["providers"]}
    assert list(by_name) == ["anthropic", "xai", "openai", "custom"]
    assert (by_name["custom"]["available"], by_name["custom"]["base_url"]) == (True, server.url)
    assert (by_name["openai"]["available"], by_name["openai"]["family"]) == (False, "openai_compatible")
    assert by_name["xai"]["family"] == "openai_compatible" and by_name["anthropic"]["family"] == "anthropic"
    tools = {m["model"]: m["tool_calling"] for m in by_name["openai"]["models"]}
    assert tools == {"gpt-6.1-sol": False, "gpt-6-luna": True}, "chat-only models are marked"
