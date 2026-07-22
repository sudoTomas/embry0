"""Credential env-var resolution for agent executors.

Single source of truth for:
- Which env vars carry auth material per auth_mode.
- Which env vars are RESERVED (never user-settable) per CLAUDE.md.

Both SDK and CLI executors call resolve_env() and merge the result into the
subprocess env. This guarantees identical credential shape across modes.
"""

from __future__ import annotations

from typing import Final, Literal

from embry0.safety.error_codes import ErrorCode

# Keys whose values are infrastructure-owned and must not be overridable
# by user repo-environment settings. See CLAUDE.md for rationale.
RESERVED_ENV_KEYS: Final[frozenset[str]] = frozenset(
    {
        "EMBRY0_GIT_PROXY_URL",
        # EMB-45: base_url the direct-xAI executor points its Anthropic SDK client at;
        # orchestrator-owned, never user-settable.
        "EMBRY0_XAI_PROXY_URL",
        # EMB-46: opt-in switch routing grok to the CLI-free DirectXaiExecutor
        # fallback instead of the default SDK-over-proxy path. Executor choice
        # is infrastructure policy — never user-settable.
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
        # storageState pre-authentication (EMB-40): both point the sandbox
        # (login command + playwright-mcp) at the orchestrator-chosen path.
        "QA_STORAGE_STATE_PATH",
        "PLAYWRIGHT_MCP_STORAGE_STATE",
        "PLAYWRIGHT_MCP_ISOLATED",
        # DinD certs are mounted by the orchestrator for dind_enabled profiles:
        "DOCKER_HOST",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    }
)


# Reserved prefixes — every key starting with these is server-controlled.
# Used by EnvVarInput key validator and by the sandbox env injection filter.
RESERVED_ENV_PREFIXES: Final[tuple[str, ...]] = (
    "QA_ARTIFACT_",  # presigned URLs minted per-attempt by init_qa
    "DOCKER_",  # broad — covers DOCKER_HOST etc. without enumerating
)


class AuthConfigError(ValueError):
    """Raised when the requested auth configuration is incoherent or missing
    required credentials. The .error_code attribute carries the canonical code.
    """

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(f"{code.value}: {message}")
        self.error_code = code


def resolve_env(
    auth_mode: Literal["api_key", "oauth"],
    *,
    api_key: str,
    oauth_token: str,
) -> dict[str, str]:
    """Return the env-var shape for the requested auth mode.

    Always sets BOTH keys — the unused one to "" — so the subprocess never
    inherits a stale credential from its parent.

    Raises AuthConfigError (with .error_code) when:
    - mode is unknown -> ERR_INVALID_CONFIG
    - mode is api_key but api_key is empty -> ERR_MISSING_API_KEY
    - mode is oauth but oauth_token is empty -> ERR_MISSING_OAUTH_TOKEN
    """
    if auth_mode == "api_key":
        if not api_key:
            raise AuthConfigError(
                ErrorCode.MISSING_API_KEY,
                "auth_mode=api_key but ANTHROPIC_API_KEY is empty",
            )
        return {
            "ANTHROPIC_API_KEY": api_key,
            "ANTHROPIC_AUTH_TOKEN": "",
            "CLAUDE_CODE_OAUTH_TOKEN": "",
        }
    if auth_mode == "oauth":
        if not oauth_token:
            raise AuthConfigError(
                ErrorCode.MISSING_OAUTH_TOKEN,
                "auth_mode=oauth but no CLAUDE_CODE_OAUTH_TOKEN available",
            )
        return {
            "CLAUDE_CODE_OAUTH_TOKEN": oauth_token,
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_AUTH_TOKEN": "",
        }
    raise AuthConfigError(
        ErrorCode.INVALID_CONFIG,
        f"unknown auth_mode: {auth_mode!r} (expected 'api_key' or 'oauth')",
    )
