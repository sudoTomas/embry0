"""Unit tests for environment parse helper + reserved-key validation."""

import pytest

from athanor.api.v1.environment import _parse_env_file


def test_parse_env_file_basic():
    parsed = _parse_env_file("# API key for X\nAPI_KEY=abc123\nMODE=prod\n")
    assert parsed == [
        {
            "key": "API_KEY",
            "default_value": "abc123",
            "description": "API key for X",
            "suggested_type": "secret",
        },
        {
            "key": "MODE",
            "default_value": "prod",
            "description": "",
            "suggested_type": "config",
        },
    ]


def test_parse_env_file_classifies_secrets():
    parsed = _parse_env_file("GITHUB_TOKEN=\nDATABASE_PASSWORD=\nPORT=8080\n")
    types = {v["key"]: v["suggested_type"] for v in parsed}
    assert types == {
        "GITHUB_TOKEN": "secret",
        "DATABASE_PASSWORD": "secret",
        "PORT": "config",
    }


def test_parse_env_file_ignores_blank_separated_comments():
    content = "# orphan comment\n\nMODE=value\n"
    parsed = _parse_env_file(content)
    assert parsed == [{"key": "MODE", "default_value": "value", "description": "", "suggested_type": "config"}]


def test_parse_env_file_strips_quotes():
    parsed = _parse_env_file("NAME=\"hello world\"\nOTHER='quoted'\n")
    values = {v["key"]: v["default_value"] for v in parsed}
    assert values == {"NAME": "hello world", "OTHER": "quoted"}


def test_parse_env_file_empty_value_is_none():
    parsed = _parse_env_file("EMPTY=\n")
    assert parsed[0]["default_value"] is None


def test_env_var_input_rejects_reserved_keys():
    """Reserved infrastructure keys (ATHANOR_GIT_PROXY_URL etc.) must be blocked
    at the API boundary — allowing them would let a user repoint the credential
    proxy URL to an attacker endpoint, exfiltrating GitHub credentials from
    every subsequent job on that repo."""
    from pydantic import ValidationError

    from athanor.api.schemas.environment import RESERVED_ENV_KEYS, EnvVarInput

    assert "ATHANOR_GIT_PROXY_URL" in RESERVED_ENV_KEYS
    assert "GITHUB_TOKEN" in RESERVED_ENV_KEYS

    with pytest.raises(ValidationError) as exc:
        EnvVarInput(key="ATHANOR_GIT_PROXY_URL", value="http://evil.example", var_type="config")
    assert "reserved" in str(exc.value).lower()

    # Non-reserved keys still work
    v = EnvVarInput(key="DATABASE_URL", value="postgres://...", var_type="config")
    assert v.key == "DATABASE_URL"


def test_init_node_drops_reserved_user_env_keys(monkeypatch):
    """Defense-in-depth: even if a reserved key somehow made it into
    user_env_vars, init_node must filter it before passing to the sandbox."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from athanor.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value="container-x")
    sandbox_mgr.destroy = AsyncMock()
    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])
    docker.run_cmd = AsyncMock(return_value="")
    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = "http://host.docker.internal:9101"

    import athanor.workflows.issue_to_pr.nodes as nodes_module

    def _noop_writer():
        return lambda _e: None

    orig = getattr(nodes_module, "get_stream_writer", None)
    nodes_module.get_stream_writer = _noop_writer
    try:
        asyncio.run(
            init_node(
                state={
                    "job_id": "job-x",
                    "repo": "o/r",
                    "user_env_vars": {
                        "ATHANOR_GIT_PROXY_URL": "http://attacker.example",
                        "SAFE_VAR": "value",
                    },
                },
                config={
                    "configurable": {
                        "sandbox_manager": sandbox_mgr,
                        "docker": docker,
                        "proxy_manager": proxy_mgr,
                    }
                },
            )
        )
    finally:
        if orig is not None:
            nodes_module.get_stream_writer = orig

    _, kwargs = sandbox_mgr.create.call_args
    env_passed = kwargs.get("env", {}) or {}
    # Attacker key was dropped; infrastructure key stayed; safe key preserved.
    assert env_passed.get("ATHANOR_GIT_PROXY_URL") == "http://host.docker.internal:9101"
    assert env_passed.get("SAFE_VAR") == "value"
    assert "http://attacker" not in str(env_passed)
