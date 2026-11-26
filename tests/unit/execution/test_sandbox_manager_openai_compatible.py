"""SandboxManager and the OpenAI-compatible providers: the sandbox env and the custom endpoint's egress."""

from unittest.mock import AsyncMock

import pytest

from embry0.execution.auth_provider import env_override_refusal
from embry0.execution.sandbox_manager import SandboxManager
from embry0.services.credentials.resolver import ResolvedCredential
from tests.unit.execution.test_sandbox_manager import (  # noqa: F401
    _stub_egress_unless_overridden,
    _stub_isolation_unless_overridden,
)
from tests.unit.execution.test_sandbox_manager_llm import _manager


def _key(provider: str, secret: str, metadata: dict[str, str] | None = None) -> ResolvedCredential:
    return ResolvedCredential(
        id=f"cred-{provider}",
        kind="llm_api_key",
        provider=provider,
        host="10.0.0.5" if provider == "custom" else "api.example.test",
        label="api-key",
        secret=secret,
        git_author_name=None,
        git_author_email=None,
        source="registry",
        trace=(),
        metadata=metadata or {},
    )


_CUSTOM = _key("custom", "ollama-placeholder", {"base_url": "http://10.0.0.5:11434/v1"})


@pytest.mark.parametrize(
    ("provider", "env_key"),
    [
        ("openai", "EMBRY0_LLM_OPENAI_API_KEY"),
        ("openrouter", "EMBRY0_LLM_OPENROUTER_API_KEY"),
        ("google", "EMBRY0_LLM_GOOGLE_API_KEY"),
        ("groq", "EMBRY0_LLM_GROQ_API_KEY"),
        ("deepseek", "EMBRY0_LLM_DEEPSEEK_API_KEY"),
    ],
)
def test_a_key_is_injected_under_its_reserved_name_only(provider: str, env_key: str) -> None:
    env = {"OPENAI_API_KEY": "the-repositorys-own-key"}
    SandboxManager._inject_llm_env(env, {provider: _key(provider, "provider-secret")})
    assert env[env_key] == "provider-secret"
    assert env["OPENAI_API_KEY"] == "the-repositorys-own-key", "a repository's own provider key is never touched"
    assert env_override_refusal(env_key, "x") is not None, f"{env_key} must be reserved from repository override"


def test_the_custom_base_url_stays_reserved() -> None:
    """The custom provider's base URL is the llm-proxy's (ADR 0032, tests/unit/execution/
    test_sandbox_manager_local_llm.py); a repository may not point an agent's conversation elsewhere."""
    assert env_override_refusal("EMBRY0_LLM_CUSTOM_BASE_URL", "http://attacker/v1") is not None, (
        "a repository must not be able to point an agent's conversation at another server"
    )


def test_a_repositorys_own_provider_variables_stay_settable() -> None:
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY"):
        assert env_override_refusal(key, "x") is None, f"{key} belongs to the repository's own app"


async def test_create_declares_nothing_more_for_a_hosted_provider() -> None:
    mgr, _, _ = _manager({"deepseek": _key("deepseek", "s")})
    mgr._runtime.attach_policy = AsyncMock()  # type: ignore[method-assign]
    await mgr.create(
        "job-1", repo="acme/widgets", llm_providers=["deepseek"], profile={"extra_networks": ["sandbox-internet"]}
    )
    assert mgr._runtime.attach_policy.await_args.kwargs["allowed_destinations"] == []
