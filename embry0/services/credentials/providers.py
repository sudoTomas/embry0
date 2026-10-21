"""LLM providers the registry can resolve (spec 2026-09-29 §3.4).

The xAI refresh-token file is an importer input, not a runtime fallback.
Anthropic's runtime fallback follows the deployment's ``DEFAULT_AUTH_MODE``:
``ANTHROPIC_API_KEY`` for ``api_key``; otherwise the OAuth token, then the
credentials file.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

# The path the orchestrator image reads today (SandboxManager._read_oauth_token).
_CLAUDE_CREDENTIALS_FILE = "/home/orchestrator/.claude/.credentials.json"


@dataclass(frozen=True)
class LlmProvider:
    name: str
    host: str
    kinds: tuple[str, ...]


LLM_PROVIDERS: dict[str, LlmProvider] = {
    "anthropic": LlmProvider("anthropic", "api.anthropic.com", ("llm_oauth", "llm_api_key")),
    "xai": LlmProvider("xai", "api.x.ai", ("llm_oauth", "llm_api_key")),
}
"""The LLM providers every deployment resolves. ``llm_providers()`` adds the enabled
OpenAI-compatible ones."""


def llm_providers(environ: Mapping[str, str] | None = None) -> dict[str, LlmProvider]:
    """This deployment's LLM providers: the two above plus those OPENAI_COMPATIBLE_PROVIDERS enables.

    An OpenAI-compatible provider takes an API key only. Its host is its API's; the
    custom provider has none of its own (each credential names its base URL).
    """
    from embry0.agents.providers import enabled_openai_compatible

    table = dict(LLM_PROVIDERS)
    for provider in enabled_openai_compatible(environ):
        host = (urlparse(provider.openai_base_url).hostname or "").lower()
        table[provider.name] = LlmProvider(provider.name, host, ("llm_api_key",))
    return table


def default_host(provider: str, environ: Mapping[str, str] | None = None) -> str:
    try:
        return llm_providers(environ)[provider].host
    except KeyError:
        raise ValueError(f"unknown llm provider {provider!r}") from None


def base_url_host(base_url: str) -> str:
    """The host a custom credential's base URL names, lowercase; '' when it names none."""
    return (urlparse(base_url).hostname or "").lower()


def env_fallback(
    provider: str,
    environ: Mapping[str, str],
    config: Any,
    *,
    credentials_file: str | None = None,
) -> tuple[str, str, str] | None:
    """``(kind, secret, env_key)`` from the process environment, or None.

    For Anthropic the kind follows ``config.default_auth_mode`` (#572): an
    api-key deployment is served ``ANTHROPIC_API_KEY`` and nothing else, so it
    is never handed an OAuth token that the agent's auth check would refuse;
    any other mode is served the OAuth token, then the credentials file. A
    repo, job or agent that overrides the mode needs a registry account.
    xAI's credential file is not read here. Every other provider is served its
    catalog ``api_key_env``; the custom provider only together with its base URL
    (``env_fallback_metadata``).
    """
    if provider not in llm_providers(environ):
        raise ValueError(f"unknown llm provider {provider!r}")
    if provider == "anthropic" and getattr(config, "default_auth_mode", None) == "api_key":
        api_key = (environ.get("ANTHROPIC_API_KEY") or "").strip()
        return ("llm_api_key", api_key, "ANTHROPIC_API_KEY") if api_key else None
    if provider == "anthropic":
        token = (environ.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
        if token:
            return ("llm_oauth", token, "CLAUDE_CODE_OAUTH_TOKEN")
        file_token = _claude_access_token(credentials_file or _CLAUDE_CREDENTIALS_FILE)
        if file_token:
            return ("llm_oauth", file_token, "claude-credentials-file")
        return None
    from embry0.agents.providers import provider_named

    catalog = provider_named(provider)
    key_env = catalog.api_key_env if catalog is not None else "XAI_API_KEY"
    key = (environ.get(key_env) or "").strip()
    if catalog is not None and catalog.base_url_env and not (environ.get(catalog.base_url_env) or "").strip():
        return None
    return ("llm_api_key", key, key_env) if key else None


def env_fallback_metadata(provider: str, environ: Mapping[str, str]) -> dict[str, Any]:
    """The non-secret settings an env-fallback credential carries: a custom provider's base URL."""
    from embry0.agents.providers import provider_named

    catalog = provider_named(provider)
    if catalog is None or not catalog.base_url_env:
        return {}
    base_url = (environ.get(catalog.base_url_env) or "").strip().rstrip("/")
    return {"base_url": base_url} if base_url else {}


def _claude_access_token(path: str) -> str | None:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path) as handle:
            raw = json.load(handle).get("claudeAiOauth", {}).get("accessToken")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    token = str(raw).strip() if raw else ""
    return token or None


def providers_for_job(
    agent_definitions: Any,
    agent_models_override: Mapping[str, str] | None,
    model_policy: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    """Providers a job must resolve before its sandbox exists.

    ``anthropic`` is always included: developer and reviewer default to Claude.
    ``provider_for_model`` returns None for a Claude model, which counts as
    anthropic. Disabled models are names the job must not run, so they do not
    pull a provider into the sandbox. The policy's ``default_model`` does:
    when set, it is the model most agents of the job resolve to (#465).
    """
    from embry0.agents.providers import provider_for_model

    models: list[str] = []
    if isinstance(agent_definitions, Mapping) and "model" not in agent_definitions:
        rows: Any = agent_definitions.values()
    elif isinstance(agent_definitions, Mapping):
        rows = [agent_definitions]
    else:
        rows = agent_definitions or []
    for row in rows:
        if isinstance(row, Mapping):
            model = row.get("model")
            if isinstance(model, str) and model.strip():
                models.append(model.strip())
    for model in (agent_models_override or {}).values():
        if isinstance(model, str) and model.strip():
            models.append(model.strip())
    default_model = (model_policy or {}).get("default_model")
    if isinstance(default_model, str) and default_model.strip():
        models.append(default_model.strip())
    tiers = (model_policy or {}).get("tier_models") or {}
    if isinstance(tiers, Mapping):
        for model in tiers.values():
            if isinstance(model, str) and model.strip():
                models.append(model.strip())
    names = {"anthropic"}
    for model in models:
        provider = provider_for_model(model)
        names.add(provider.name if provider is not None else "anthropic")
    return tuple(sorted(names))
