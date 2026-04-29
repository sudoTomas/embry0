import os
from unittest.mock import patch

from athanor.config import AthanorConfig


def test_config_defaults():
    """Config loads with sensible defaults when no env vars set."""
    with patch.dict(os.environ, {}, clear=True):
        config = AthanorConfig(
            _env_file=None,  # type: ignore[call-arg]
        )
    assert config.database_url == "postgresql://athanor:athanor@localhost:5432/athanor"
    assert config.max_budget_usd == 10.0
    assert config.daily_budget_cap_usd == 100.0
    assert config.monthly_budget_cap_usd == 500.0
    assert config.sandbox_memory == "8g"
    assert config.sandbox_cpus == "4"
    assert config.provider_mode == "anthropic_api"


def test_dead_env_var_is_silently_ignored():
    """Unknown env vars (like the deleted max_global_concurrent_jobs) must not raise."""
    env = {"MAX_GLOBAL_CONCURRENT_JOBS": "5"}
    with patch.dict(os.environ, env, clear=True):
        cfg = AthanorConfig(_env_file=None)  # type: ignore[call-arg]
    # Pydantic extra="ignore" means this just works; no assertion needed beyond no exception.
    assert cfg is not None


def test_config_from_env():
    """Config reads from environment variables."""
    env = {
        "DATABASE_URL": "postgresql://user:pass@db:5432/mydb",
        "GITHUB_TOKEN": "ghp_test123",
        "MAX_BUDGET_USD": "25.0",
        "PROVIDER_MODE": "anthropic_api",
        "ANTHROPIC_API_KEY": "sk-ant-test",
    }
    with patch.dict(os.environ, env, clear=True):
        config = AthanorConfig(
            _env_file=None,  # type: ignore[call-arg]
        )
    assert config.database_url == "postgresql://user:pass@db:5432/mydb"
    assert config.github_token == "ghp_test123"
    assert config.max_budget_usd == 25.0
    assert config.anthropic_api_key == "sk-ant-test"


def test_config_cors_origins_parsing():
    """Comma-separated CORS origins are parsed into a list."""
    env = {"ALLOWED_CORS_ORIGINS": "http://localhost:3001, http://example.com"}
    with patch.dict(os.environ, env, clear=True):
        config = AthanorConfig(_env_file=None)  # type: ignore[call-arg]
    assert config.allowed_cors_origins_list == ["http://localhost:3001", "http://example.com"]


def test_config_cors_origins_default():
    """Empty CORS origins defaults to localhost:3001."""
    with patch.dict(os.environ, {}, clear=True):
        config = AthanorConfig(_env_file=None)  # type: ignore[call-arg]
    assert config.allowed_cors_origins_list == ["http://localhost:3001"]


def test_config_trigger_labels_parsing():
    """Comma-separated trigger labels are parsed into a list."""
    env = {"TRIGGER_LABELS": "Athanor, auto-fix, bot"}
    with patch.dict(os.environ, env, clear=True):
        config = AthanorConfig(_env_file=None)  # type: ignore[call-arg]
    assert config.trigger_labels_list == ["Athanor", "auto-fix", "bot"]


def test_config_claude_max_provider():
    """Claude Max provider mode reads OAuth token."""
    env = {
        "PROVIDER_MODE": "claude_max",
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-test",
    }
    with patch.dict(os.environ, env, clear=True):
        config = AthanorConfig(_env_file=None)  # type: ignore[call-arg]
    assert config.provider_mode == "claude_max"
    assert config.claude_code_oauth_token == "sk-ant-oat01-test"


def test_config_ollama_provider():
    """Ollama provider mode reads base URL and model."""
    env = {
        "PROVIDER_MODE": "ollama",
        "OLLAMA_BASE_URL": "http://gpu:11434",
        "OLLAMA_MODEL": "codellama",
    }
    with patch.dict(os.environ, env, clear=True):
        config = AthanorConfig(_env_file=None)  # type: ignore[call-arg]
    assert config.provider_mode == "ollama"
    assert config.ollama_base_url == "http://gpu:11434"
    assert config.ollama_model == "codellama"


def test_legacy_dev_mode_shim_respects_explicit_false():
    """If operator explicitly sets new flags to False, legacy DEV_MODE must NOT override."""
    env = {"DEV_MODE": "true", "AUTH_DEV_MODE": "false", "WEBHOOK_DEV_MODE": "false"}
    with patch.dict(os.environ, env, clear=True):
        cfg = AthanorConfig(_env_file=None)  # type: ignore[call-arg]
    assert cfg.auth_dev_mode is False
    assert cfg.webhook_dev_mode is False


def test_legacy_dev_mode_shim_triggers_when_only_dev_mode_set():
    """Bare DEV_MODE=true (legacy operator) → both flags flipped on."""
    # Ensure the new flags are NOT in the environment at all
    base = {k: v for k, v in os.environ.items() if k not in ("AUTH_DEV_MODE", "WEBHOOK_DEV_MODE")}
    base["DEV_MODE"] = "true"
    with patch.dict(os.environ, base, clear=True):
        cfg = AthanorConfig(_env_file=None)  # type: ignore[call-arg]
    assert cfg.auth_dev_mode is True
    assert cfg.webhook_dev_mode is True


def test_legacy_dev_mode_shim_partial_explicit():
    """If only one of the new flags is explicit, legacy must not override either."""
    # AUTH_DEV_MODE is explicitly set; WEBHOOK_DEV_MODE is absent.
    base = {k: v for k, v in os.environ.items() if k not in ("AUTH_DEV_MODE", "WEBHOOK_DEV_MODE")}
    base["DEV_MODE"] = "true"
    base["AUTH_DEV_MODE"] = "true"
    with patch.dict(os.environ, base, clear=True):
        cfg = AthanorConfig(_env_file=None)  # type: ignore[call-arg]
    # Operator explicitly set at least one new flag → honour new flags, ignore legacy shim.
    # AUTH_DEV_MODE=true was explicit; WEBHOOK_DEV_MODE absent defaults to False.
    assert cfg.auth_dev_mode is True
    assert cfg.webhook_dev_mode is False
