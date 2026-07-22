"""auth_provider.resolve_env — env-var shape per auth_mode."""

import pytest

from embry0.execution.auth_provider import (
    RESERVED_ENV_KEYS,
    AuthConfigError,
    resolve_env,
)


def test_api_key_mode_sets_api_key_and_unsets_oauth() -> None:
    env = resolve_env("api_key", api_key="sk-test-abc", oauth_token="unused")
    assert env["ANTHROPIC_API_KEY"] == "sk-test-abc"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""


def test_oauth_mode_sets_oauth_and_unsets_api_key() -> None:
    env = resolve_env("oauth", api_key="unused", oauth_token="oat-xyz")
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oat-xyz"
    assert env["ANTHROPIC_API_KEY"] == ""
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""


def test_api_key_mode_rejects_empty_key() -> None:
    with pytest.raises(AuthConfigError) as exc:
        resolve_env("api_key", api_key="", oauth_token="")
    assert "ERR_MISSING_API_KEY" in str(exc.value)


def test_oauth_mode_rejects_empty_token() -> None:
    with pytest.raises(AuthConfigError) as exc:
        resolve_env("oauth", api_key="", oauth_token="")
    assert "ERR_MISSING_OAUTH_TOKEN" in str(exc.value)


def test_unknown_mode_rejected() -> None:
    with pytest.raises(AuthConfigError) as exc:
        resolve_env("cookies", api_key="x", oauth_token="y")
    assert "ERR_INVALID_CONFIG" in str(exc.value)


def test_reserved_env_keys_export_expected_set() -> None:
    # These must NEVER be user-settable via the environment UI.
    expected = {
        "EMBRY0_GIT_PROXY_URL",
        # EMB-45: base_url for the direct-xAI executor's proxy.
        "EMBRY0_XAI_PROXY_URL",
        # EMB-46: opt-in switch for the CLI-free direct-executor fallback.
        "EMBRY0_XAI_DIRECT_EXECUTOR",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        # EMB-36: non-Anthropic provider keys are orchestrator-owned too.
        "XAI_API_KEY",
        "GITHUB_TOKEN",
        # QA-injected infrastructure (orchestrator owns these):
        "QA_JOB_ID",
        "QA_ATTEMPT_N",
        "QA_NETWORK_NAME",
        # storageState pre-authentication (EMB-40):
        "QA_STORAGE_STATE_PATH",
        "PLAYWRIGHT_MCP_STORAGE_STATE",
        "PLAYWRIGHT_MCP_ISOLATED",
        # DinD certs are mounted by the orchestrator for dind_enabled profiles:
        "DOCKER_HOST",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    }
    assert RESERVED_ENV_KEYS == expected


def test_reserved_env_prefixes_export_expected_tuple() -> None:
    from embry0.execution.auth_provider import RESERVED_ENV_PREFIXES

    # These prefixes must NEVER be user-settable via the environment UI.
    assert RESERVED_ENV_PREFIXES == ("QA_ARTIFACT_", "DOCKER_")
