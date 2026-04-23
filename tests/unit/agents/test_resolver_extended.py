"""resolve_agent_invocation — five-level precedence + validation."""

import pytest

from legion.agents.resolver import resolve_agent_invocation
from legion.execution.auth_provider import AuthConfigError
from legion.safety.error_codes import ErrorCode


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


def test_oauth_without_token_rejected() -> None:
    with pytest.raises(AuthConfigError) as exc:
        resolve_agent_invocation(
            **_base_args(
                global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
                credentials={"api_key": "sk-abc", "oauth_token": ""},
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
                "tools": ["Read"],
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
