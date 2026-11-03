"""resolve_agent_invocation — five-level precedence + validation."""

import pytest

from embry0.agents.resolver import resolve_agent_invocation
from embry0.execution.auth_provider import AuthConfigError
from embry0.safety.error_codes import ErrorCode


def _base_args(**over):  # noqa: ANN002
    defaults = {
        "agent_type": "developer",
        "prompt": "do stuff",
        "system_context": "",
        "global_defaults": {"execution_mode": "sdk", "auth_mode": "oauth"},
        "repo_prefs": None,
        "job_overrides": None,
        "agent_definition": {
            "model": "claude-sonnet-4-6",
            "tools": ["Read", "Write", "Edit", "Bash"],
            "skills": [],
            "system_prompt": "",
            "mcp_servers": {},
            "execution_mode": None,
            "auth_mode": None,
        },
        "pipeline_config": {},
        "max_turns": 10,
        "timeout_seconds": 60,
        "credentials": {"api_key": "", "oauth_token": "oat-xyz"},
    }
    defaults.update(over)
    return defaults


def test_global_defaults_apply_when_nothing_overrides() -> None:
    inv = resolve_agent_invocation(**_base_args())
    assert inv.execution_mode == "sdk"
    assert inv.auth_mode == "oauth"


def test_repo_pref_auth_mode_beats_global() -> None:
    inv = resolve_agent_invocation(
        **_base_args(
            repo_prefs={"execution_mode": None, "auth_mode": "api_key"},
            credentials={"api_key": "sk-abc", "oauth_token": ""},
        )
    )
    assert inv.auth_mode == "api_key"
    assert inv.execution_mode == "sdk"  # global wins since repo is None


def test_job_override_beats_repo() -> None:
    inv = resolve_agent_invocation(
        **_base_args(
            repo_prefs={"execution_mode": None, "auth_mode": "oauth"},
            job_overrides={"execution_mode": None, "auth_mode": "api_key"},
            credentials={"api_key": "sk-abc", "oauth_token": "oat"},
        )
    )
    assert inv.auth_mode == "api_key"


def test_per_agent_type_beats_job() -> None:
    inv = resolve_agent_invocation(
        **_base_args(
            job_overrides={"execution_mode": None, "auth_mode": "oauth"},
            agent_definition={
                "model": "claude-sonnet-4-6",
                "tools": ["Read"],
                "skills": [],
                "system_prompt": "",
                "mcp_servers": {},
                "execution_mode": None,
                "auth_mode": "api_key",
            },
            credentials={"api_key": "sk-abc", "oauth_token": "oat"},
        )
    )
    assert inv.auth_mode == "api_key"


def test_pipeline_config_beats_per_agent_type() -> None:
    inv = resolve_agent_invocation(
        **_base_args(
            agent_definition={
                "model": "claude-sonnet-4-6",
                "tools": ["Read"],
                "skills": [],
                "system_prompt": "",
                "mcp_servers": {},
                "execution_mode": None,
                "auth_mode": "api_key",
            },
            pipeline_config={
                "auth_modes": {"developer": "oauth"},
            },
            credentials={"api_key": "sk-abc", "oauth_token": "oat"},
        )
    )
    assert inv.auth_mode == "oauth"


def test_cli_mode_rejected_in_phase1() -> None:
    with pytest.raises(AuthConfigError) as exc:
        resolve_agent_invocation(
            **_base_args(
                global_defaults={"execution_mode": "cli", "auth_mode": "oauth"},
            )
        )
    assert exc.value.error_code == ErrorCode.INVALID_CONFIG


def test_api_key_without_credentials_rejected() -> None:
    with pytest.raises(AuthConfigError) as exc:
        resolve_agent_invocation(
            **_base_args(
                global_defaults={"execution_mode": "sdk", "auth_mode": "api_key"},
                credentials={"api_key": "", "oauth_token": "oat"},
            )
        )
    assert exc.value.error_code == ErrorCode.MISSING_API_KEY


def test_api_key_rejects_resolved_oauth_account() -> None:
    """Both strings are present, so only the resolved kind can fail this."""
    try:
        resolve_agent_invocation(
            **_base_args(
                global_defaults={"execution_mode": "sdk", "auth_mode": "api_key"},
                credentials={
                    "api_key": "sk-abc",
                    "oauth_token": "oat",
                    "anthropic_kind": "llm_oauth",
                },
            )
        )
    except AuthConfigError as exc:
        assert exc.error_code == ErrorCode.MISSING_API_KEY, (
            f"auth_mode=api_key with a resolved llm_oauth must be ERR_MISSING_API_KEY, got {exc.error_code}"
        )
    else:
        raise AssertionError("auth_mode=api_key with a resolved llm_oauth must raise ERR_MISSING_API_KEY")


def test_oauth_rejects_resolved_api_key_account() -> None:
    """Both strings are present, so only the resolved kind can fail this."""
    try:
        resolve_agent_invocation(
            **_base_args(
                global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
                credentials={
                    "api_key": "sk-abc",
                    "oauth_token": "oat",
                    "anthropic_kind": "llm_api_key",
                },
            )
        )
    except AuthConfigError as exc:
        assert exc.error_code == ErrorCode.MISSING_OAUTH_TOKEN, (
            f"auth_mode=oauth with a resolved llm_api_key must be ERR_MISSING_OAUTH_TOKEN, got {exc.error_code}"
        )
    else:
        raise AssertionError("auth_mode=oauth with a resolved llm_api_key must raise ERR_MISSING_OAUTH_TOKEN")


def test_oauth_without_token_rejected() -> None:
    with pytest.raises(AuthConfigError) as exc:
        resolve_agent_invocation(
            **_base_args(
                global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
                credentials={"api_key": "sk-abc", "oauth_token": ""},
            )
        )
    assert exc.value.error_code == ErrorCode.MISSING_OAUTH_TOKEN


def test_provider_routed_model_skips_anthropic_credential_check() -> None:
    # RAV-1240: a grok agent never uses the Anthropic credentials — the
    # executor overlays provider auth and strips the Anthropic vars — so
    # resolution must succeed with NO Anthropic credentials at all.
    inv = resolve_agent_invocation(
        **_base_args(
            pipeline_config={"agent_models": {"developer": "grok-4.5"}},
            credentials={"api_key": "", "oauth_token": ""},
        )
    )
    assert inv.model == "grok-4.5"
    assert inv.provider == "xai"


def test_anthropic_model_still_requires_credentials() -> None:
    # The RAV-1240 skip is provider-routed models ONLY: an Anthropic model
    # with empty credentials keeps failing exactly as before.
    with pytest.raises(AuthConfigError) as exc:
        resolve_agent_invocation(
            **_base_args(
                credentials={"api_key": "", "oauth_token": ""},
            )
        )
    assert exc.value.error_code == ErrorCode.MISSING_OAUTH_TOKEN


def test_skills_resolved_from_agent_definition() -> None:
    inv = resolve_agent_invocation(
        **_base_args(
            agent_definition={
                "model": "claude-sonnet-4-6",
                "tools": ["Read"],
                "skills": ["code-review"],
                "system_prompt": "",
                "mcp_servers": {},
                "execution_mode": None,
                "auth_mode": None,
            },
        )
    )
    assert inv.skills == ["code-review"]


def test_mcp_servers_resolved() -> None:
    inv = resolve_agent_invocation(
        **_base_args(
            agent_definition={
                "model": "claude-sonnet-4-6",
                "tools": ["Read", "Bash"],
                "skills": [],
                "system_prompt": "",
                "mcp_servers": {"my-mcp": {"command": "npx", "args": ["mymcp"]}},
                "execution_mode": None,
                "auth_mode": None,
            },
        )
    )
    assert inv.mcp_servers == {"my-mcp": {"command": "npx", "args": ["mymcp"]}}


def test_system_prompt_resolved_from_agent_definition() -> None:
    inv = resolve_agent_invocation(
        **_base_args(
            agent_definition={
                "model": "claude-sonnet-4-6",
                "tools": ["Read"],
                "skills": [],
                "system_prompt": "You are a reviewer",
                "mcp_servers": {},
                "execution_mode": None,
                "auth_mode": None,
            },
        )
    )
    assert inv.system_prompt == "You are a reviewer"


def test_a_pipeline_system_prompt_is_ignored() -> None:
    """pipeline_config.system_prompts[agent_type] never replaces agent_definition.system_prompt."""
    inv = resolve_agent_invocation(
        **_base_args(
            agent_definition={
                "model": "claude-sonnet-4-6",
                "tools": ["Read"],
                "skills": [],
                "system_prompt": "from agent definition",
                "mcp_servers": {},
                "execution_mode": None,
                "auth_mode": None,
            },
            pipeline_config={"system_prompts": {"developer": "from pipeline"}},
        )
    )
    assert inv.system_prompt == "from agent definition", f"triage replaced the system prompt: {inv.system_prompt!r}"


def test_grok_agent_gets_twice_the_turns_and_time() -> None:
    # grok-4.7 makes ~3x Claude's tool calls per developer run (median 111 vs 37) and runs
    # ~2x as long at p90. On Claude-sized budgets 4 of its first 8 developer runs died at the
    # turn cap (2026-10-03/04). The provider scales the budget once the model is known.
    inv = resolve_agent_invocation(
        **_base_args(
            pipeline_config={"agent_models": {"developer": "grok-4.7"}},
            max_turns=40,
            timeout_seconds=1800,
        )
    )
    assert inv.provider == "xai"
    assert (inv.max_turns, inv.timeout_seconds) == (80, 3600), "grok budget must be scaled by its provider"


def test_anthropic_agent_budget_is_unchanged() -> None:
    inv = resolve_agent_invocation(**_base_args(max_turns=40, timeout_seconds=1800))
    assert inv.provider is None
    assert (inv.max_turns, inv.timeout_seconds) == (40, 1800)
